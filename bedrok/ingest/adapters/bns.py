"""Bureau of National Statistics of Kazakhstan adapter: the Taldau API (no key).

`source_code` is an indicator id and the parameters that pin one series:

  703076?period=4&measure=1&terms=741880,2695743,4772381&dics=67,848,2753

  period   Taldau's period type: 4 month, 5 quarter, 9 quarter to date
           (year-to-date figures at each quarter's end), 7 year.
  measure  the indicator's `measureId` from GetIndexAttributes. A wrong one
           answers an empty body, not an error.
  terms    one term per dimension (GetSegmentList, or the data call itself
           with idx=<dimension> lists a dimension's elements); 741880 is the
           Republic of Kazakhstan, 2695730 / 2695732 / 2695743 compare with
           the previous period / the same period a year earlier / December
           2010.
  dics     the dimensions' directory ids, in the same order.

and, optionally, Bedrok's own transformations, each stated in the series'
notes:

  as=change         the bureau publishes changes as a relative (109.3 for a
                    9.3% rise): subtract 100.
  ytd=decumulate    quarterly figures published year to date: Q1 as published,
                    each later quarter minus the one before it in the same
                    year. A quarter whose predecessor is missing is dropped.
                    Only for levels: a growth rate cannot be de-cumulated.
  scale=1e-9        multiply (tenge to billions of tenge).

  GET https://taldau.stat.gov.kz/en/Api/GetIndexTreeData?p_measure_id=…
      &p_index_id=…&p_period_id=…&p_terms=…&p_term_id=741880&p_dicIds=…
      &idx=0&p_parent_id=

answers a list of rows whose first is the Republic of Kazakhstan; values sit
in keys `yMMYYYY` (MM is the period's last month), as strings, and "x" marks
a suppressed figure.

Licence: official statistical information may be used for any purpose,
freely and free of charge, with a reference to the source
(https://stat.gov.kz/en/description/). Attribution.
"""
from __future__ import annotations

import functools
import re
from datetime import date
from urllib.parse import parse_qsl

import pandas as pd
import requests

API = "https://taldau.stat.gov.kz/en/Api/GetIndexTreeData"
KAZAKHSTAN = "741880"
QUARTERLY = {"5", "9"}
TIMEOUT = 60
_KEY = re.compile(r"y(\d{2})(\d{4})")


def fetch(source_code: str, start: str | None = None) -> pd.DataFrame:
    index, _, query = source_code.partition("?")
    opts = dict(parse_qsl(query))
    for need in ("period", "measure", "terms", "dics"):
        if need not in opts:
            raise ValueError(f"BNS: source_code needs {need}=")
    rows = _download(int(index), opts["period"], opts["measure"], opts["terms"], opts["dics"])
    df = parse(rows, opts["period"], opts.get("as"), opts.get("ytd"), float(opts.get("scale", 1)))
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df


@functools.lru_cache(maxsize=64)
def _download(index: int, period: str, measure: str, terms: str, dics: str):
    """One GET per series per run. Cleared by pipeline.clear_download_caches()."""
    r = requests.get(API, timeout=TIMEOUT, params={
        "p_measure_id": measure, "p_index_id": index, "p_period_id": period,
        "p_terms": terms, "p_term_id": KAZAKHSTAN, "p_dicIds": dics, "idx": 0,
        "p_parent_id": ""})
    r.raise_for_status()
    if not r.text.strip():
        raise ValueError("BNS: empty answer (check the measure id against GetIndexAttributes)")
    return r.json()


def _period_start(key: str, period: str) -> date | None:
    m = _KEY.fullmatch(key)
    if not m:
        return None
    month, year = int(m.group(1)), int(m.group(2))
    if period in QUARTERLY:
        month -= 2                       # the key names the quarter's last month
    return date(year, month, 1) if 1 <= month <= 12 else None


def parse(rows, period: str, as_: str | None = None, ytd: str | None = None,
          scale: float = 1.0) -> pd.DataFrame:
    """Taldau rows → the Republic of Kazakhstan's series as [date, value]."""
    row = next((r for r in rows or [] if str(r.get("id")) == KAZAKHSTAN), None)
    if row is None:
        raise ValueError("BNS: no row for the Republic of Kazakhstan in the answer")
    pairs = []
    for key, raw in row.items():
        d = _period_start(key, period)
        if d is None or raw in (None, "", "x"):
            continue
        pairs.append((d, float(str(raw).replace(",", "."))))
    df = pd.DataFrame(pairs, columns=["date", "value"]).sort_values("date").reset_index(drop=True)
    if ytd == "decumulate":
        df = _decumulate(df)
    if as_ == "change":
        df["value"] = df["value"] - 100
    # Rounded to drop float residue (109.3 - 100 is 9.299999...), far below
    # the one or two decimals the bureau publishes.
    df["value"] = (df["value"] * scale).round(9)
    return df


def _decumulate(df: pd.DataFrame) -> pd.DataFrame:
    """Year-to-date quarterly levels → discrete quarters."""
    held = dict(zip(df["date"], df["value"]))
    out = []
    for d, v in held.items():
        if d.month == 1:
            out.append((d, v))
            continue
        before = date(d.year, d.month - 3, 1)
        if before in held:
            out.append((d, v - held[before]))
    return pd.DataFrame(out, columns=["date", "value"]).sort_values("date").reset_index(drop=True)
