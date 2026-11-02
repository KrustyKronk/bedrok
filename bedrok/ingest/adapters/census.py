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

Construction spending (C30) is the exception, read from its workbook rather
than the API. The API's `vip` dataset carries about sixteen coded categories,
none of them the data-centre line, which exists only in the published
spreadsheets (checked 2026-11-02). `source_code` is then
"c30:<workbook>:<column heading>", e.g.

  c30:privtime:Data center   private construction, data centres, NSA, from 2014

Workbook traps, confirmed against the live file on 2026-11-02:
  * one sheet; the header row is the one whose first cell is "Date", below
    three rows of title and notes, and footnotes follow the data.
  * headings carry a carriage-return escape and a footnote digit
    ("Total\n_x000D_Private Construction1"), so they are compared cleaned.
  * dates are "Sep-26" with a "p" (preliminary) or "r" (revised) suffix,
    newest first; a line that did not exist yet is an empty string.
  * the workbook needs no key, but the source row does: a keyless install
    skips it along with the API series.

Licence: public domain — a US government work.
"""
from __future__ import annotations

import functools
import io
import re
from datetime import date

import pandas as pd
import requests

from bedrok.ingest.adapters._util import BROWSER_UA

BASE = "https://api.census.gov/data/timeseries/eits/"
C30_URL = "https://www.census.gov/construction/c30/xlsx/{book}.xlsx"
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


def _heading(cell) -> str:
    """A workbook heading as words: escapes, line breaks and footnote digits gone."""
    text = str(cell or "").replace("_x000D_", " ")
    return " ".join(re.sub(r"\d+$", "", text.strip()).split()).lower()


def _parse_c30(content: bytes, column: str) -> pd.DataFrame:
    """Pure: a C30 workbook → one column as DataFrame[date, value]."""
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(content), read_only=True,
                                data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    top = next((i for i, r in enumerate(rows) if str(r[0]).strip() == "Date"), None)
    if top is None:
        raise ValueError("C30 workbook: no header row starting 'Date'")
    heads = [_heading(c) for c in rows[top]]
    if _heading(column) not in heads:
        raise ValueError(f"C30 workbook has no column {column!r}")
    j = heads.index(_heading(column))
    df = pd.DataFrame([(r[0], r[j]) for r in rows[top + 1:] if len(r) > j],
                      columns=EMPTY)
    label = df["date"].astype(str).str.strip().str.rstrip("pr")
    df["date"] = pd.to_datetime(label, format="%b-%y", errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna()
    df["date"] = df["date"].dt.date
    return (df.drop_duplicates(subset=["date"], keep="first")
              .sort_values("date").reset_index(drop=True))[EMPTY]


@functools.lru_cache(maxsize=4)
def _download(book: str) -> bytes:
    r = requests.get(C30_URL.format(book=book), headers=BROWSER_UA, timeout=120)
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        raise ValueError(f"the C30 workbook {book}.xlsx is gone: Census answered "
                         f"{r.headers.get('content-type', 'something else')}")
    return r.content


def fetch(source_code: str, api_key: str, start: str | None = None) -> pd.DataFrame:
    if source_code.startswith("c30:"):
        _, book, column = source_code.split(":", 2)
        df = _parse_c30(_download(book), column)
        if start and not df.empty:
            df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
        return df
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
