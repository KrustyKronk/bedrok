"""Eurostat adapter (no API key). JSON-stat 2.0 dissemination API.

Docs: https://ec.europa.eu/eurostat/web/main/data/web-services
`source_code` is "DATASET?filter=val&filter=val", e.g.
  "prc_hicp_manr?coicop=CP00&geo=EA"
Returns a tidy DataFrame[date, value]. Only the `time` dimension is expected to
vary once filters are applied; other dimensions are collapsed to their first
category (JSON-stat row-major index maths handles the general case safely).
"""
from __future__ import annotations

import pandas as pd
import requests

from bedrok.ingest.adapters._util import parse_period

BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"


def fetch(source_code: str, start: str | None = None) -> pd.DataFrame:
    dataset, _, query = source_code.partition("?")
    url = f"{BASE}/{dataset}"
    params: dict[str, str] = {"format": "JSON"}
    for part in filter(None, query.split("&")):
        k, _, v = part.partition("=")
        params[k] = v
    if start:
        params["sinceTimePeriod"] = start
    r = requests.get(url, params=params, timeout=45)
    r.raise_for_status()
    return _parse_jsonstat(r.json())


def _parse_jsonstat(js: dict) -> pd.DataFrame:
    dims = js["id"]
    sizes = js["size"]
    if "time" not in dims:
        return pd.DataFrame(columns=["date", "value"])
    t_axis = dims.index("time")

    # strides for row-major flattening
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    time_cat = js["dimension"]["time"]["category"]["index"]
    pos_to_time = {pos: label for label, pos in time_cat.items()}

    rows = []
    for flat_str, val in js["value"].items():
        flat = int(flat_str)
        t_pos = (flat // strides[t_axis]) % sizes[t_axis]
        rows.append((pos_to_time[t_pos], val))

    df = pd.DataFrame(rows, columns=["date", "value"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"] = parse_period(df["date"])
    df = df.dropna(subset=["value", "date"]).sort_values("date").reset_index(drop=True)
    df["date"] = df["date"].dt.date
    return df


def fetch_multi_geo(source_code: str, start: str | None = None):
    """Fetch a dataset WITHOUT a geo filter and split it per geo.

    `source_code` is "DATASET?filter=val&..." (no geo param). Returns
    (per_geo, labels): per_geo maps geo code → DataFrame[date, value];
    labels maps geo code → human name. One API call for all of Europe —
    the reliable alternative to per-country web fetches.
    """
    dataset, _, query = source_code.partition("?")
    params: dict[str, str] = {"format": "JSON"}
    for part in filter(None, query.split("&")):
        k, _, v = part.partition("=")
        params[k] = v
    if start:
        params["sinceTimePeriod"] = start
    r = requests.get(f"{BASE}/{dataset}", params=params, timeout=120)
    r.raise_for_status()
    js = r.json()

    dims, sizes = js["id"], js["size"]
    if "time" not in dims or "geo" not in dims:
        return {}, {}
    t_axis, g_axis = dims.index("time"), dims.index("geo")
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    time_cat = js["dimension"]["time"]["category"]["index"]
    pos_to_time = {pos: label for label, pos in time_cat.items()}
    geo_cat = js["dimension"]["geo"]["category"]
    pos_to_geo = {pos: code for code, pos in geo_cat["index"].items()}
    labels = dict(geo_cat.get("label", {}))

    rows: dict[str, list] = {}
    for flat_str, val in js["value"].items():
        flat = int(flat_str)
        geo = pos_to_geo[(flat // strides[g_axis]) % sizes[g_axis]]
        t = pos_to_time[(flat // strides[t_axis]) % sizes[t_axis]]
        rows.setdefault(geo, []).append((t, val))

    per_geo = {}
    for geo, data in rows.items():
        df = pd.DataFrame(data, columns=["date", "value"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df["date"] = parse_period(df["date"])
        df = df.dropna(subset=["value", "date"]).sort_values("date").reset_index(drop=True)
        df["date"] = df["date"].dt.date
        if not df.empty:
            per_geo[geo] = df
    return per_geo, labels
