"""JODI oil adapter: the Joint Organisations Data Initiative's World Database
(no key).

`source_code` is `<REF_AREA>/<ENERGY_PRODUCT>/<FLOW_BREAKDOWN>/<UNIT_MEASURE>`
with an optional first month, e.g. `KZ/CRUDEOIL/INDPROD/KBD?from=2016-01`:
Kazakhstan's crude oil production in thousand barrels a day from January
2016.

JODI publishes the primary-products database as one CSV per year,

  https://www.jodidata.org/_resources/files/downloads/oil-data/annual-csv/primary/<year>.csv

with the current year as `primaryyear<year>.csv` (the name may change once
the year is over, so a 404 tries the other one). Each file is 8 to 12 MB and
holds every country; columns are REF_AREA, TIME_PERIOD (YYYY-MM),
ENERGY_PRODUCT, FLOW_BREAKDOWN, UNIT_MEASURE, OBS_VALUE, ASSESSMENT_CODE.
A month not yet reported is "-", a figure never reported "N/A"; both are
dropped. The single zip on the downloads page is a Beyond 20/20 binary, not
CSV. The database is updated on or around the 20th of each month.

Licence: "freely available"; cite as International Energy Forum (IEF), Joint
Organizations Data Initiative (JODI) Database
(https://www.jodidata.org/oil/support/user-guide.aspx). Attribution.
"""
from __future__ import annotations

import csv
import functools
import io
from datetime import date
from urllib.parse import parse_qsl

import pandas as pd
import requests

from bedrok.ingest.adapters._util import BROWSER_UA

BASE = "https://www.jodidata.org/_resources/files/downloads/oil-data/annual-csv/primary"
FIRST_YEAR = 2002
TIMEOUT = 180


def fetch(source_code: str, start: str | None = None, today: date | None = None) -> pd.DataFrame:
    key, _, query = source_code.partition("?")
    parts = key.split("/")
    if len(parts) != 4:
        raise ValueError(f"JODI: source_code needs AREA/PRODUCT/FLOW/UNIT, got {key!r}")
    first = dict(parse_qsl(query)).get("from")
    lower = max([d for d in (first, str(start)[:7] if start else None) if d] or [f"{FIRST_YEAR}-01"])
    this_year = (today or date.today()).year
    rows = []
    for year in range(max(int(lower[:4]), FIRST_YEAR), this_year + 1):
        rows += parse(_year(year, year == this_year), *parts)
    df = pd.DataFrame(rows, columns=["date", "value"])
    if df.empty:
        raise ValueError(f"JODI: no rows for {key}")
    df = (df[df["date"] >= date(int(lower[:4]), int(lower[5:7]), 1)]
          .drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True))
    return df


def _year(year: int, current: bool) -> str:
    names = [f"primaryyear{year}.csv", f"{year}.csv"]
    if not current:
        names.reverse()
    for i, name in enumerate(names):
        try:
            return _download(f"{BASE}/{name}")
        except requests.HTTPError as e:
            missing = getattr(e.response, "status_code", None) == 404
            if missing and i == 0:
                continue
            # A new year's file exists only after JODI's first update of the
            # year (about the 20th); until then that year simply has no rows.
            # A past year missing under both names is an error.
            if missing and current:
                return ""
            raise
    raise AssertionError("unreachable")


@functools.lru_cache(maxsize=32)
def _download(url: str) -> str:
    """One GET per yearly file per run: crude and NGL share them. Cleared by
    pipeline.clear_download_caches()."""
    r = requests.get(url, headers=BROWSER_UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def parse(text: str, area: str, product: str, flow: str, unit: str) -> list[tuple]:
    """One yearly CSV → [(month start, value)] for one series."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 6 or row[0] != area or row[2] != product or row[3] != flow or row[4] != unit:
            continue
        try:
            value = float(row[5])
            month = date(int(row[1][:4]), int(row[1][5:7]), 1)
        except ValueError:
            continue                      # "-" not yet reported, "N/A" never reported
        out.append((month, value))
    return out
