"""BEA (Bureau of Economic Analysis) API, NIPA dataset. Free key required.

US-government public-domain national accounts: real GDP and the PCE price
indexes. `source_code` is "<table>:<line>:<freq>[:<scale>]", e.g.

  T10106:1:Q:billions   real GDP, chained dollars (FRED GDPC1)
  T20804:1:M            PCE price index (FRED PCEPI)
  T20804:25:M           PCE price index ex food and energy (FRED PCEPILFE)

BEA reports each value with a UNIT_MULT exponent (6 = millions). `scale`
names the unit the catalog stores — the value is multiplied out by UNIT_MULT
and divided back down — so a table published in millions lands in billions,
as FRED and the catalog have it. Price indexes carry no scale and no multiplier.

One table request returns every line, so the table is cached per process:
PCE and core PCE cost one call between them.

Limits: 100 requests and 30 errors a minute per key; beyond that BEA answers
429 and locks the key out for about a minute.

Licence: public domain — a US government work.
"""
from __future__ import annotations

import functools

import pandas as pd
import requests

URL = "https://apps.bea.gov/api/data"
SCALES = {"units": 1, "thousands": 1e3, "millions": 1e6, "billions": 1e9}
EMPTY = ["date", "value"]


def _period(tp: str):
    """BEA TimePeriod → month-start date: 2025Q3 → 2025-07-01, 2025M08 → 2025-08-01."""
    if "Q" in tp:
        y, q = tp.split("Q")
        return pd.Timestamp(int(y), 3 * int(q) - 2, 1).date()
    if "M" in tp:
        y, m = tp.split("M")
        return pd.Timestamp(int(y), int(m), 1).date()
    return pd.Timestamp(int(tp), 1, 1).date()


def _parse(rows: list[dict], line: str, scale: str | None = None) -> pd.DataFrame:
    """Pure: BEA Data rows → one line as DataFrame[date, value]."""
    out = []
    for r in rows or []:
        if str(r.get("LineNumber")) != str(line):
            continue
        raw = str(r.get("DataValue", "")).replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        if scale:
            v = v * 10 ** int(r.get("UNIT_MULT") or 0) / SCALES[scale]
        out.append((_period(r["TimePeriod"]), v))
    df = pd.DataFrame(out, columns=EMPTY)
    return (df.drop_duplicates(subset=["date"], keep="last")
              .sort_values("date").reset_index(drop=True))


@functools.lru_cache(maxsize=8)
def _table(table: str, freq: str, api_key: str) -> tuple:
    r = requests.get(URL, timeout=120, params={
        "UserID": api_key, "method": "GetData", "DataSetName": "NIPA",
        "TableName": table, "Frequency": freq, "Year": "X", "ResultFormat": "JSON"})
    r.raise_for_status()
    body = r.json().get("BEAAPI") or {}
    results = body.get("Results") or {}
    if isinstance(results, list):
        results = results[0] if results else {}
    err = body.get("Error") or results.get("Error")
    if err:
        raise RuntimeError(f"BEA error: {err}")
    return tuple(results.get("Data") or [])


def fetch(source_code: str, api_key: str, start: str | None = None) -> pd.DataFrame:
    parts = source_code.split(":")
    table, line, freq = parts[0], parts[1], parts[2]
    scale = parts[3] if len(parts) > 3 else None
    df = _parse(list(_table(table, freq, api_key)), line, scale)
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df
