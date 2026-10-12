"""NBER business-cycle reference dates, turned into a monthly recession flag.

  https://data.nber.org/cycles/business_cycle_dates.json

The file is a JSON list of {"peak": "YYYY-MM-DD", "trough": "YYYY-MM-DD"}; the
first entry has an empty peak (the 1854 trough). The flag follows FRED's USREC
convention, checked against it on 2026-10-10: a month is 1 from the month
AFTER a peak through the trough month inclusive (February 2020 peak, April
2020 trough → March and April are 1). The months before the first trough are
not dated by the NBER, so the series starts at that trough.

Licence: attribution — "Permission to copy is granted, provided attribution of
source is given" (nber.org).
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import requests

from bedrok.ingest.adapters._util import BROWSER_UA

URL = "https://data.nber.org/cycles/business_cycle_dates.json"


def recession_months(cycles: list[dict], through: date) -> pd.DataFrame:
    """Pure: peak/trough pairs → monthly DataFrame[date, value] of 0/1."""
    troughs = [pd.Timestamp(c["trough"]) for c in cycles if c.get("trough")]
    months = pd.date_range(min(troughs), pd.Timestamp(through), freq="MS")
    flag = pd.Series(0.0, index=months)
    for c in cycles:
        if not c.get("peak"):
            if c.get("trough"):
                flag[pd.Timestamp(c["trough"])] = 1.0
            continue
        start = pd.Timestamp(c["peak"]) + pd.DateOffset(months=1)
        end = pd.Timestamp(c["trough"]) if c.get("trough") else months[-1]
        flag[(flag.index >= start) & (flag.index <= end)] = 1.0
    return pd.DataFrame({"date": flag.index.date, "value": flag.values})


def fetch_cycles() -> list[dict]:
    r = requests.get(URL, headers=BROWSER_UA, timeout=60)
    r.raise_for_status()
    return r.json()
