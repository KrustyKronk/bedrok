"""Japan e-Stat adapter (free application id, ESTAT_API_KEY).

`source_code` is a statistics table id and its pinned classes, as a query
string of e-Stat's own `cd…` parameters:

  0004052037?cdTab=1&cdCat01=0161&cdArea=00000   (CPI less fresh food, 2025 base)
  0002060004?cdCat02=0&cdCat03=08&cdCat04=00     (unemployment rate, from 1968)

  GET https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData
      ?appId=…&statsDataId=…&lang=E&metaGetFlg=N&cdCat01=…

Time codes are ten digits: YYYY00MMMM is a month (2026000808 = Aug 2026),
YYYY000000 a calendar year and YYYY100000 a fiscal year. Tables mix the three;
only months are kept. Every returned value must share one set of classes, or
the answer is refused as unpinned.

The CPI is re-based every five years into a NEW table (2020 base 0003427113,
2025 base 0004052037), each carrying the linked history back to 1970; the old
table stops. Japan's "core" CPI is all items less FRESH FOOD only (cat01 0161),
not the Western core less food and energy (0178).

Licence: Government of Japan Standard Terms of Use (compatible with CC BY 4.0).
The appId travels as a URL parameter, so errors pass through sources.redact.
"""
from __future__ import annotations

import pandas as pd
import requests

from bedrok.ingest.adapters._util import UnpinnedKey

BASE = "https://api.e-stat.go.jp/rest/3.0/app/json"


def fetch(source_code: str, api_key: str, start: str | None = None) -> pd.DataFrame:
    table, _, query = source_code.partition("?")
    params = {"appId": api_key, "statsDataId": table, "lang": "E",
              "metaGetFlg": "N", "cntGetFlg": "N"}
    params.update(part.split("=", 1) for part in filter(None, query.split("&")))
    r = requests.get(f"{BASE}/getStatsData", params=params, timeout=120)
    r.raise_for_status()
    df = _parse(r.json())
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df


def _month_start(code: str):
    """'2026000808' → 2026-08-01; years and fiscal years → None."""
    code = str(code)
    if len(code) != 10 or code[4:6] != "00" or code[6:8] != code[8:10] or code[6:8] == "00":
        return None
    return pd.Timestamp(int(code[:4]), int(code[6:8]), 1).date()


def _parse(js: dict) -> pd.DataFrame:
    """Pure: getStatsData JSON → monthly [date, value]."""
    data = js["GET_STATS_DATA"]
    status = data["RESULT"]["STATUS"]
    if status != 0:
        raise ValueError(f"e-Stat: {status} {data['RESULT']['ERROR_MSG']}")
    values = data["STATISTICAL_DATA"]["DATA_INF"]["VALUE"]
    values = values if isinstance(values, list) else [values]
    keys = {tuple(sorted((k, v) for k, v in o.items() if k.startswith("@") and k not in ("@time", "@unit")))
            for o in values}
    if len(keys) > 1:
        raise UnpinnedKey(f"e-Stat selection holds {len(keys)} series, e.g. {sorted(keys)[:2]}: pin them")
    df = pd.DataFrame([(_month_start(o["@time"]), o.get("$")) for o in values],
                      columns=["date", "value"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return (df.dropna(subset=["date", "value"])
              .drop_duplicates(subset="date", keep="last")
              .sort_values("date").reset_index(drop=True))
