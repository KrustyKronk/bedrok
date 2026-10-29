"""National Bank of Kazakhstan adapter (no API key).

The bank serves its statistics three ways, and the history lives in different
ones, so `source_code` names which:

  records:<section>/<slug>#<field>
      A statistics page's own JSON. GET https://nationalbank.kz/en/<section>/
      <slug>/records answers the page's whole table, every month since the
      1990s, as a list of rows; <field> is one of its columns
      (`m_3`, `gross_international_reserve_volume_million_dollar`). Each row
      is dated by the month of its `reporting_date`: recent rows are stamped
      at the month's end, older ones on an arbitrary day inside the month.
      Checked against the IMF on 2026-10-28: M3 equals the IMF's broad money
      to the million in every month compared, and reserves match within the
      gold-valuation differences, with no month shift.
  api:<formId>?<column>=<value>&...
      The open-data API (data.nationalbank.kz/api/v1, a beta): long-format
      rows filtered on the named columns, which must leave one row per date.
      Most of its tables start only in 2018 to 2023; the effective exchange
      rates (form 299, from 1995) and inflation expectations (form 305, from
      2016) are the ones worth taking from it.
  base-rate
      The base rate's decisions, one row per decision, dated by the day it
      took effect (from 2015-09-02, when the base rate replaced the
      refinancing rate as the policy rate).
  fx:<rate id>
      Official exchange rates from the rates report page (5 = USD, 6 = EUR),
      KZT per unit, daily from 1999-10-19. A rate is set the day before the
      date it applies to, so the newest row is dated tomorrow.

`next_release(source_code)` reads a statistics page's "Next:" date for the
release hook (ingest/pipeline.py); it is an extra request, made only after a
`records:` series has been fetched.

Licence: free reuse with reference to the source, software included
(https://nationalbank.kz/en/page/data-usage-terms). Attribution.
"""
from __future__ import annotations

import functools
import html
import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl

import pandas as pd
import requests

from bedrok.ingest.adapters._util import UnpinnedKey

SITE = "https://nationalbank.kz/en"
API = "https://data.nationalbank.kz/api/v1/data"
FX_REPORT = f"{SITE}/exchangerates/ezhednevnye-oficialnye-rynochnye-kursy-valyut/report"
FX_FIRST = date(1999, 10, 1)
PAGE_SIZE = 10000
TIMEOUT = 60
HEADERS = {"Accept-Language": "en"}


def fetch(source_code: str, start: str | None = None) -> pd.DataFrame:
    kind, _, rest = source_code.partition(":")
    if kind == "records":
        page, _, field = rest.partition("#")
        df = parse_records(_download(f"{SITE}/{page}/records"), field)
    elif kind == "api":
        form, _, query = rest.partition("?")
        df = parse_api(_download(f"{API}?formId={int(form)}&pageSize={PAGE_SIZE}"),
                       dict(parse_qsl(query)))
    elif kind == "base-rate":
        today = date.today()
        df = parse_base_rate(_download(f"{API}/base-rate?from=1990-01-01&to={today:%Y-%m-%d}"))
    elif kind == "fx":
        first = max(pd.Timestamp(start).date(), FX_FIRST) if start else FX_FIRST
        last = date.today() + timedelta(days=1)
        url = (f"{FX_REPORT}?beginDate={first:%d.%m.%Y}&endDate={last:%d.%m.%Y}"
               f"&rates[]={int(rest)}")
        df = parse_fx(_download(url, as_text=True))
    else:
        raise ValueError(f"NBK: unknown source_code form {source_code!r}")
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df


@functools.lru_cache(maxsize=64)
def _download(url: str, as_text: bool = False):
    """One GET per URL per run: the three reserves series share one page.
    Cleared by pipeline.clear_download_caches()."""
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text if as_text else r.json()


def _frame(pairs) -> pd.DataFrame:
    df = pd.DataFrame(pairs, columns=["date", "value"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "value"]).sort_values("date").reset_index(drop=True)
    return df


def _rows(js) -> list:
    """A `/records` answer is a list; tolerate it wrapped in an object."""
    if isinstance(js, list):
        return js
    if isinstance(js, dict):
        for v in js.values():
            if isinstance(v, list):
                return v
    raise ValueError("NBK: no rows in the answer")


def parse_records(js, field: str) -> pd.DataFrame:
    """A statistics page's rows → one column, dated by its reporting month.
    Two rows in one month would be two answers for one period: refuse."""
    rows = _rows(js)
    if rows and field not in rows[-1]:
        raise ValueError(f"NBK: no column {field!r} in the table")
    seen, pairs = set(), []
    for row in rows:
        stamp = str(row.get("reporting_date") or "")[:10]
        if not stamp:
            continue
        d = date.fromisoformat(stamp).replace(day=1)
        if d in seen:
            raise ValueError(f"NBK: two rows for {d:%Y-%m}")
        seen.add(d)
        pairs.append((d, row.get(field)))
    return _frame(pairs)


def parse_api(js: dict, filters: dict) -> pd.DataFrame:
    """Open-data API rows → the one series the filters pin."""
    rows = js.get("rows") or []
    total = js.get("totalRows")
    if total is not None and int(total) > len(rows):
        raise ValueError(f"NBK: the answer was paged ({len(rows)} of {total} rows)")
    keep = [r for r in rows if all(str(r.get(k)) == v for k, v in filters.items())]
    dates = [r.get("report_date") for r in keep]
    if len(dates) != len(set(dates)):
        others = sorted({k for r in keep for k in r} - {"report_date", "amount", *filters})
        raise UnpinnedKey(f"NBK: several rows per date; pin one of {others}")
    return _frame([(date.fromisoformat(str(r["report_date"])[:10]), r.get("amount"))
                   for r in keep])


def parse_base_rate(js) -> pd.DataFrame:
    """The base rate's decisions → one row per decision, dated when it took effect."""
    if not isinstance(js, list):
        raise ValueError("NBK: the base-rate answer is not a list of decisions")
    return _frame([(date.fromisoformat(str(r["effectiveDate"])[:10]), r.get("rate"))
                   for r in js if r.get("effectiveDate")])


_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)


def _cells(row: str) -> list[str]:
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in _TD.findall(row)]


def parse_fx(text: str) -> pd.DataFrame:
    """The rates report's table → KZT per one unit: each row is a date, the
    quantity the rate is quoted for and the rate, so divide."""
    pairs = []
    for row in _TR.findall(text):
        cells = _cells(row)
        if len(cells) < 3 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            continue
        qty = float(cells[1].replace(",", "."))
        rate = float(cells[2].replace(" ", "").replace(",", "."))
        pairs.append((date.fromisoformat(cells[0]), rate / qty))
    if not pairs:
        raise ValueError("NBK: no rates in the report (the page changed?)")
    return _frame(pairs).drop_duplicates(subset="date", keep="last").reset_index(drop=True)


_NEXT = re.compile(r"Next:\s*(\d{2}\.\d{2}\.\d{4})")


def parse_next(text: str) -> date | None:
    """A statistics page's "Next: 30.10.2026", or None."""
    m = _NEXT.search(re.sub(r"<[^>]+>", " ", text or ""))
    return datetime.strptime(m.group(1), "%d.%m.%Y").date() if m else None


def next_release(source_code: str) -> date | None:
    """The date the page behind a `records:` series names for its next
    publication. Other forms have no such page."""
    kind, _, rest = source_code.partition(":")
    if kind != "records":
        return None
    page = rest.partition("#")[0]
    return parse_next(_download(f"{SITE}/{page}", as_text=True))
