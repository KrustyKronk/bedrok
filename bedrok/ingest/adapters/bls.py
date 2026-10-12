"""BLS (Bureau of Labor Statistics) Public Data API v2. Key optional.

US-government public-domain data: CPI, PPI, import prices, payrolls and
earnings (CES), the household survey (CPS), JOLTS and the employment cost index.
`source_code` is the BLS series id itself (e.g. CUSR0000SA0).

The API answers many series per request but few requests per day, so the unit
of work is a *window of years for every BLS series in the run*, not a series:

                 per day   series/request   years/request
  no key            25          25               10
  BLS_API_KEY      500          50               20

`prime(codes)` tells the adapter which series the run will ask for; each
`fetch(code, start)` then reads from windows that were requested once for the
whole primed set and cached. A full keyless backfill of the ~17 catalog series
is twelve requests (1913 to today in ten-year windows); a refresh is one. Without
priming, fetch still works — it just spends one request per series per window.

Format traps, confirmed against the live API on 2026-10-09:
  * period M13 is the annual average and Q05 does not exist; only M01-M12 and
    Q01-Q04 are observations.
  * a missing observation is the string "-" (the October 2025 CPI, never
    collected during the shutdown, is one) — coerced to NaN and dropped.
  * the request can "succeed" with REQUEST_NOT_PROCESSED when the daily
    threshold is hit; that is raised, never read as an empty series.
  * ECI: CIU… is NOT seasonally adjusted, CIS… is. FRED's ECIALLCIV is CIS.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import requests

URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
FIRST_YEAR = 1913  # CPI and PPI all commodities start here; nothing in the catalog is older

_primed: tuple[str, ...] = ()
_windows: dict[tuple, dict[str, list[dict]]] = {}


def limits(api_key: str) -> tuple[int, int]:
    """(series per request, years per request) for this key state."""
    return (50, 20) if api_key else (25, 10)


def prime(codes) -> None:
    """Declare every BLS series the coming run will fetch, and drop the cache."""
    global _primed
    _primed = tuple(sorted(set(codes)))
    _windows.clear()


def _period_date(year: str, period: str) -> date | None:
    if period.startswith("M") and period != "M13":
        return date(int(year), int(period[1:]), 1)
    if period.startswith("Q") and period in ("Q01", "Q02", "Q03", "Q04"):
        return date(int(year), 3 * int(period[1:]) - 2, 1)
    return None


def _parse(data: list[dict]) -> pd.DataFrame:
    """Pure: BLS `data` rows → DataFrame[date, value], ascending, deduped."""
    rows = []
    for d in data or []:
        when = _period_date(d.get("year", ""), d.get("period", ""))
        if when is not None:
            rows.append((when, d.get("value")))
    df = pd.DataFrame(rows, columns=["date", "value"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = (df.dropna(subset=["value"])
            .drop_duplicates(subset=["date"], keep="last")
            .sort_values("date").reset_index(drop=True))
    return df[["date", "value"]]


def _request(codes: list[str], y0: int, y1: int, api_key: str) -> dict[str, list[dict]]:
    body = {"seriesid": codes, "startyear": str(y0), "endyear": str(y1)}
    if api_key:
        body["registrationkey"] = api_key
    r = requests.post(URL, json=body, timeout=60)
    r.raise_for_status()
    payload = r.json()
    if payload.get("status") != "REQUEST_SUCCEEDED":
        hint = "" if api_key else " (keyless: 25 requests a day — set BLS_API_KEY for 500)"
        raise RuntimeError(f"BLS {payload.get('status')}{hint}: {payload.get('message')}")
    return {s["seriesID"]: s.get("data") or []
            for s in (payload.get("Results") or {}).get("series") or []}


def _windows_for(y0: int, y1: int, span: int):
    while y0 <= y1:
        yield y0, min(y0 + span - 1, y1)
        y0 += span


def fetch(code: str, api_key: str = "", start: str | None = None) -> pd.DataFrame:
    per_req, span = limits(api_key)
    batch = list(_primed) if code in _primed else [code]
    y_start = pd.Timestamp(start).year if start else FIRST_YEAR
    collected: list[dict] = []
    for y0, y1 in _windows_for(y_start, date.today().year, span):
        for i in range(0, len(batch), per_req):
            chunk = tuple(batch[i:i + per_req])
            if code not in chunk:
                continue
            key = (chunk, y0, y1, bool(api_key))
            if key not in _windows:
                _windows[key] = _request(list(chunk), y0, y1, api_key)
            collected.extend(_windows[key].get(code, []))
    df = _parse(collected)
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df
