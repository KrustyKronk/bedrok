"""Federal Reserve research-data file downloads, and FHFA's (no API key).

These are static files on Fed research sites rather than an API, so one adapter
covers them all and the registry below is the only thing that changes when a
file moves. Institution is the unstable axis, not format: the Weekly Economic
Index started life as a New York Fed product and is now published by the Dallas
Fed, which is exactly why this is `fedfiles` and not `nyfed`.

`source_code` is "<file key>:<column>", e.g. "acm:ACMTP10".

Format traps, all confirmed against the live files:
  * ACM and GSCPI are legacy OLE2 .xls — openpyxl cannot read them, so they
    need xlrd. GSCPI is served from a URL ending .xlsx but is really .xls
    (its own content-disposition says so).
  * GSCPI dates are month-END; they are normalised to month-START to match how
    every other monthly series in the store is dated, so joins line up.
  * MCT ships four columns all named "MCT" — the point estimate is the middle
    one, with a 68% band either side (verified: the third column lies inside
    [col2, col4] in all 797 rows).

More traps, from the regional Fed files added in session 3b (2026-10-10):
  * the Chicago Fed's old chicagofed.org/-/media/… URLs still answer 200 but
    are FROZEN at April 2026; the live files are on api.data.chicagofed.org.
    Only a staleness check catches that, never a status code.
  * Cleveland's median/trimmed CPI: the "_ann_" column is rounded to one
    decimal; FRED's value is ((1 + monthly change)^12 - 1) * 100, recomputed
    here. The date header carries a trailing space.
  * Cleveland's expectations are decimals (0.026), not percent.
  * Dallas's trimmed-mean PCE has its header in row 4 and writes "#NAN".
  * the Atlanta Fed sheet repeats its column names, so the column is found by
    position after its block's title.
  * the Philadelphia Fed writes dates as Mon-YY from May-68: "%y" reads 68 as
    2068, so years in the future move back a century.

Licence is per catalog row, not per file: the NY Fed is attribution; the
Chicago, Cleveland, Dallas and Atlanta Feds allow reuse only non-commercially
and with credit (noncommercial); the Philadelphia Fed reserves all rights
(personal_use); FHFA is a US government work (public_domain, with its notice).
See bedrok/core/licensing.py.
"""
from __future__ import annotations

import csv
import functools
import io

import pandas as pd
import requests

from bedrok.ingest.adapters._util import BROWSER_UA

EMPTY = ["date", "value"]


def _read_xls(content: bytes, sheet: str, **kw) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(content), engine="xlrd", sheet_name=sheet, **kw)


def _tidy(df: pd.DataFrame, month_start: bool = False) -> pd.DataFrame:
    df = df.rename(columns={df.columns[0]: "date", df.columns[1]: "value"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce", format="mixed")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "value"])
    if month_start:
        df["date"] = df["date"].values.astype("datetime64[M]")
    df = (df.drop_duplicates(subset="date", keep="last")
            .sort_values("date").reset_index(drop=True))
    df["date"] = df["date"].dt.date
    return df[EMPTY]


def _parse_acm(content: bytes, column: str) -> pd.DataFrame:
    """ACM term premia. One 10MB workbook carries fitted yields (ACMY01-10),
    term premia (ACMTP01-10) and risk-neutral yields (ACMRNY01-10)."""
    df = _read_xls(content, "ACM Daily", usecols=["DATE", column])
    return _tidy(df[["DATE", column]])


def _parse_gscpi(content: bytes, column: str = "GSCPI") -> pd.DataFrame:
    raw = _read_xls(content, "GSCPI Monthly Data", header=None)
    header = raw.iloc[0].tolist()
    col = header.index(column)
    return _tidy(raw.iloc[1:, [0, col]], month_start=True)


def _parse_wei(content: bytes, column: str = "WEI") -> pd.DataFrame:
    """The '2008-current' sheet holds the series; the first sheet is front
    matter, and the other columns are frozen vintages we deliberately ignore."""
    df = pd.read_excel(io.BytesIO(content), engine="openpyxl",
                       sheet_name="2008-current")
    return _tidy(df[["Date", column]])


def _parse_mct(content: bytes, column: str = "MCT") -> pd.DataFrame:
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    names = rows[3]                       # rows 0-2 are chart-routing metadata
    if column == "MCT":
        idx = 3                           # middle of four "MCT" columns = point estimate
    else:
        idx = names.index(column)
    data = [(r[1], r[idx]) for r in rows[4:] if len(r) > idx and r[1].strip()]
    return _tidy(pd.DataFrame(data, columns=EMPTY))


def _csv(content: bytes, **kw) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(content), **kw)


def _parse_nfci(content: bytes, column: str = "NFCI") -> pd.DataFrame:
    df = _csv(content, usecols=["Friday_of_Week", column])
    return _tidy(df)


def _parse_cfnai(content: bytes, column: str = "CFNAI") -> pd.DataFrame:
    df = _csv(content, usecols=["Date", column])
    df["Date"] = pd.to_datetime(df["Date"], format="%Y/%m", errors="coerce")
    return _tidy(df)


def _parse_cleveland_cpi(content: bytes, column: str) -> pd.DataFrame:
    """column is the file's monthly-change column (mcpi_monthly_chg or
    trim_monthly_chg); the value returned is annualised, as FRED shows it."""
    df = _csv(content)
    df.columns = [c.strip() for c in df.columns]
    df = df[["date", column]].copy()
    df[column] = ((1 + pd.to_numeric(df[column], errors="coerce")) ** 12 - 1) * 100
    return _tidy(df)


def _parse_cleveland_expectations(content: bytes, column: str = "1") -> pd.DataFrame:
    df = pd.read_excel(io.BytesIO(content), engine="openpyxl",
                       sheet_name="Expected Inflation")
    df.columns = [str(c).strip() for c in df.columns]
    col = f"{column} year Expected Inflation"
    df = df[["Model Output Date", col]].copy()
    df[col] = pd.to_numeric(df[col], errors="coerce") * 100
    return _tidy(df)


def _parse_dallas_pce(content: bytes, column: str = "12-month") -> pd.DataFrame:
    raw = pd.read_excel(io.BytesIO(content), engine="openpyxl",
                        sheet_name="Web - Historical", header=None)
    header = raw.iloc[3].tolist()
    return _tidy(raw.iloc[4:, [0, header.index(column)]])


def _parse_atlanta_sticky(content: bytes, column: str = "Core Sticky CPI (monthly)") -> pd.DataFrame:
    """The 12-month rate sits three columns after its block's title."""
    raw = pd.read_excel(io.BytesIO(content), engine="openpyxl", sheet_name="Data",
                        header=None)
    col = raw.iloc[0].tolist().index(column) + 3
    return _tidy(raw.iloc[1:, [0, col]])


def _parse_philly_bos(content: bytes, column: str = "GAC") -> pd.DataFrame:
    df = _csv(content, usecols=["DATE", column])
    dates = pd.to_datetime(df["DATE"], format="%b-%y", errors="coerce")
    future = dates > pd.Timestamp.today()
    dates[future] = dates[future] - pd.DateOffset(years=100)
    df["DATE"] = dates
    return _tidy(df)


def _parse_fhfa(content: bytes, column: str = "USA") -> pd.DataFrame:
    """hpi_master.csv is long: one row per index, place and period. column is
    the place_id; the value is the purchase-only, monthly, seasonally adjusted
    index."""
    df = _csv(content, usecols=["hpi_flavor", "frequency", "place_id", "yr",
                                "period", "index_sa"])
    df = df[(df["hpi_flavor"] == "purchase-only") & (df["frequency"] == "monthly")
            & (df["place_id"] == column)]
    dates = pd.to_datetime(dict(year=df["yr"], month=df["period"], day=1), errors="coerce")
    return _tidy(pd.DataFrame({"date": dates, "value": df["index_sa"]}))


FILES: dict[str, dict] = {
    "acm": {
        "url": "https://www.newyorkfed.org/medialibrary/media/research/"
               "data_indicators/ACMTermPremium.xls",
        "parse": _parse_acm,
    },
    "gscpi": {
        "url": "https://www.newyorkfed.org/medialibrary/research/interactives/"
               "gscpi/downloads/gscpi_data.xlsx",
        "parse": _parse_gscpi,
    },
    "mct": {
        "url": "https://www.newyorkfed.org/medialibrary/Research/Interactives/"
               "Data/mct/mct-chart-data.csv",
        "parse": _parse_mct,
    },
    "wei": {
        "url": "https://www.dallasfed.org/-/media/documents/research/wei/"
               "weekly-economic-index.xlsx",
        "parse": _parse_wei,
    },
    "nfci": {
        "url": "https://api.data.chicagofed.org/NFCI/nfci-data-series-csv.csv",
        "parse": _parse_nfci,
    },
    "cfnai": {
        "url": "https://api.data.chicagofed.org/CFNAI/cfnai-data-series-csv.csv",
        "parse": _parse_cfnai,
    },
    "clev_median": {
        "url": "https://www.clevelandfed.org/-/media/files/webcharts/mediancpi/"
               "mcpi_revised.csv",
        "parse": _parse_cleveland_cpi,
    },
    "clev_trim": {
        "url": "https://www.clevelandfed.org/-/media/files/webcharts/mediancpi/"
               "trim_revised.csv",
        "parse": _parse_cleveland_cpi,
    },
    "clev_exp": {
        "url": "https://www.clevelandfed.org/-/media/files/webcharts/"
               "inflationexpectations/inflation-expectations.xlsx",
        "parse": _parse_cleveland_expectations,
    },
    "dallas_pce": {
        "url": "https://www.dallasfed.org/~/media/documents/research/pce/pcehist",
        "parse": _parse_dallas_pce,
    },
    "atl_sticky": {
        "url": "https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/"
               "datafiles/research/inflationproject/stickprice/stickyprice.xlsx",
        "parse": _parse_atlanta_sticky,
    },
    "philly_bos": {
        "url": "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/"
               "MBOS/Historical-Data/Diffusion-Indexes/bos_dif.csv",
        "parse": _parse_philly_bos,
    },
    "fhfa_hpi": {
        "url": "https://www.fhfa.gov/hpi/download/monthly/hpi_master.csv",
        "parse": _parse_fhfa,
    },
}


@functools.lru_cache(maxsize=16)
def _download(url: str) -> bytes:
    """Cached per process so pulling several columns out of the same workbook
    (ACM is ~10MB and carries 30 series) costs one download, not thirty."""
    r = requests.get(url, headers=BROWSER_UA, timeout=180)
    r.raise_for_status()
    return r.content


def fetch(source_code: str, start: str | None = None) -> pd.DataFrame:
    key, _, column = source_code.partition(":")
    spec = FILES.get(key)
    if spec is None:
        raise ValueError(f"unknown fedfiles key {key!r} — expected one of {sorted(FILES)}")
    df = spec["parse"](_download(spec["url"]), column) if column else \
        spec["parse"](_download(spec["url"]))
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df
