"""New York Fed: the Markets Data API and the Empire State survey file. No key.

`source_code` is one of:
  sofr            Secured overnight financing rate, percent (from 2018-04)
  effr            Effective federal funds rate, percent, daily (from 2000-07)
  effr_target_lower / effr_target_upper
                  The FOMC's target, as the same feed carries it each day: a
                  range from 2008-12-16, a single rate before. The single
                  target is reported as the lower bound and the upper bound
                  is empty there, because no range existed. A decision is a
                  day on which these change.
  rrp_tsy         Overnight reverse repo, Treasury collateral accepted, billions
                  USD (from 2003) — FRED's RRPONTSYD
  esms:<column>   Empire State Manufacturing Survey, seasonally adjusted
                  diffusion indexes; GACDISA is general business conditions

Format traps, confirmed against the live endpoints on 2026-10-09:
  * a day can carry more than one reverse-repo operation (2024-2026 has several
    such days), so accepted amounts are summed per operation date; operations
    also carry MBS and agency tranches, which FRED's series excludes.
  * amounts are in dollars; FRED and the catalog use billions.
  * the Empire file dates each survey to month-END; the store dates monthly
    series to month-START, so dates are normalised.
  * newyorkfed.org answers a default HTTP-client agent with 403 (see _util).

Licence: attribution — NY Fed Terms of Use, which also carry a disclaimer for
the reference rates (SOFR).
"""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
import requests

from bedrok.ingest.adapters._util import BROWSER_UA

MARKETS = "https://markets.newyorkfed.org/api"
ESMS_URL = ("https://www.newyorkfed.org/medialibrary/media/survey/empire/data/"
            "esms_seasonallyadjusted_diffusion.csv")
EMPTY = ["date", "value"]


def _tidy(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=EMPTY)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = (df.dropna(subset=["date", "value"])
            .drop_duplicates(subset=["date"], keep="last")
            .sort_values("date").reset_index(drop=True))
    return df[EMPTY]


def _parse_sofr(payload: dict) -> pd.DataFrame:
    rows = [(pd.Timestamp(r["effectiveDate"]).date(), r.get("percentRate"))
            for r in payload.get("refRates") or [] if r.get("type") == "SOFR"]
    return _tidy(pd.DataFrame(rows, columns=EMPTY))


_EFFR_FIELD = {"effr": "percentRate", "effr_target_lower": "targetRateFrom",
               "effr_target_upper": "targetRateTo"}


def _parse_effr(payload: dict, key: str) -> pd.DataFrame:
    field = _EFFR_FIELD[key]
    rows = [(pd.Timestamp(r["effectiveDate"]).date(), r.get(field))
            for r in payload.get("refRates") or [] if r.get("type") == "EFFR"]
    return _tidy(pd.DataFrame(rows, columns=EMPTY))


def _parse_rrp(payload: dict) -> pd.DataFrame:
    totals: dict[date, float] = {}
    for op in (payload.get("repo") or {}).get("operations") or []:
        if op.get("operationType") != "Reverse Repo" or op.get("term") != "Overnight":
            continue
        day = pd.Timestamp(op["operationDate"]).date()
        tsy = sum(float(d.get("amtAccepted") or 0) for d in op.get("details") or []
                  if d.get("securityType") == "Treasury")
        totals[day] = totals.get(day, 0.0) + tsy
    rows = [(d, v / 1e9) for d, v in totals.items()]
    return _tidy(pd.DataFrame(rows, columns=EMPTY))


def _parse_esms(text: str, column: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text), usecols=["surveyDate", column])
    df = df.rename(columns={"surveyDate": "date", column: "value"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df["date"] = df["date"].values.astype("datetime64[M]")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return _tidy(df)


def _get(url: str, params: dict | None = None) -> requests.Response:
    r = requests.get(url, params=params, headers=BROWSER_UA, timeout=90)
    r.raise_for_status()
    return r


def fetch(source_code: str, start: str | None = None) -> pd.DataFrame:
    key, _, column = source_code.partition(":")
    window = {"startDate": start or "2000-01-01", "endDate": date.today().isoformat()}
    if key == "sofr":
        df = _parse_sofr(_get(f"{MARKETS}/rates/secured/sofr/search.json", window).json())
    elif key in _EFFR_FIELD:
        df = _parse_effr(_get(f"{MARKETS}/rates/unsecured/effr/search.json", window).json(), key)
    elif key == "rrp_tsy":
        df = _parse_rrp(_get(f"{MARKETS}/rp/results/search.json",
                             {**window, "operationTypes": "reverserepo",
                              "term": "overnight"}).json())
    elif key == "esms":
        df = _parse_esms(_get(ESMS_URL).text, column or "GACDISA")
    else:
        raise ValueError(f"unknown nyfed code {source_code!r}")
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df
