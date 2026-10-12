"""EIA (US Energy Information Administration) adapter. Free API key required.

US-government public-domain energy data — WTI, Brent, Henry Hub and refined
products — freely reusable, no conditions attached.

Uses the v2 /seriesid/ passthrough route (stable for years); source_code is the
plain legacy series id (e.g. PET.RWTC.D). Keeping it plain means a future route
change touches only this adapter. Returns a tidy DataFrame[date, value].

The API caps each response at 5000 rows, so deep daily series (WTI since 1986 is
~10k rows) MUST be paginated — offset-paged here until `total` is covered.
"""
from __future__ import annotations

import pandas as pd
import requests

BASE = "https://api.eia.gov/v2/seriesid/"
PAGE = 5000  # EIA hard cap per response


def _parse(data: list[dict]) -> pd.DataFrame:
    """Pure: EIA data rows → DataFrame[date(py date), value(float)], asc, deduped."""
    if not data:
        return pd.DataFrame(columns=["date", "value"])
    df = pd.DataFrame(data)[["period", "value"]].rename(columns={"period": "date"})
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = (df.dropna(subset=["value"])
            .drop_duplicates(subset=["date"])
            .sort_values("date")
            .reset_index(drop=True))
    return df[["date", "value"]]


def fetch(series_code: str, api_key: str, start: str | None = None) -> pd.DataFrame:
    params = {"api_key": api_key, "length": PAGE, "offset": 0}
    if start:
        params["start"] = start
    rows: list[dict] = []
    while True:
        params["offset"] = len(rows)
        r = requests.get(BASE + series_code, params=params, timeout=45)
        r.raise_for_status()
        resp = r.json().get("response") or {}
        data = resp.get("data") or []
        rows.extend(data)
        total = int(resp.get("total") or 0)
        if len(data) < PAGE or len(rows) >= total:
            break
    return _parse(rows)
