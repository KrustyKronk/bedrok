"""FRED (St. Louis Fed) adapter. Free API key required.

Docs: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
Returns a tidy DataFrame[date, value] for a single series code.
"""
from __future__ import annotations

import pandas as pd
import requests

BASE = "https://api.stlouisfed.org/fred/series/observations"


def fetch(series_code: str, api_key: str, start: str | None = None) -> pd.DataFrame:
    params = {
        "series_id": series_code,
        "api_key": api_key,
        "file_type": "json",
    }
    if start:
        params["observation_start"] = start
    r = requests.get(BASE, params=params, timeout=30)
    r.raise_for_status()
    obs = r.json().get("observations", [])
    if not obs:
        return pd.DataFrame(columns=["date", "value"])
    df = pd.DataFrame(obs)[["date", "value"]]
    # FRED encodes missing values as ".".
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.dropna(subset=["value"]).reset_index(drop=True)
    return df
