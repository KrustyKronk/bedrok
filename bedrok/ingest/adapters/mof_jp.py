"""Japan's Ministry of Finance: the JGB interest-rate curve (no key).

`source_code` is a tenor column as the ministry heads it: "10Y", "2Y", "30Y"...

The ministry publishes the constant-maturity curve as two CSV files, both
Shift-JIS, two header lines (a title, then `Date,1Y,2Y,...`):

  historical/jgbcme_all.csv   every business day from 1974-09-24 to the end
                              of the previous month
  jgbcme.csv                  the current month so far

so a series is the two read together, the current file winning on any date
both carry. Dates are written 2026/9/30; a tenor not yet issued or not quoted
that day is "-" (the 10-year starts in 1986). The files end with a note line
in Japanese, which is not a date and is dropped.

Licence: Public Data License 1.0, the Japanese government's standard terms
(reuse with the source credited; compatible with CC BY 4.0). Terms:
https://www.mof.go.jp/english/about_mof/notice/index.html
"""
from __future__ import annotations

import io
import re

import pandas as pd
import requests

from bedrok.ingest.adapters._util import BROWSER_UA

BASE = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate"
FILES = (f"{BASE}/historical/jgbcme_all.csv", f"{BASE}/jgbcme.csv")


def fetch(source_code: str, start: str | None = None) -> pd.DataFrame:
    frames = []
    for url in FILES:
        r = requests.get(url, headers=BROWSER_UA, timeout=120)
        r.raise_for_status()
        frames.append(_parse(r.content.decode("shift_jis", errors="replace"), source_code))
    df = (pd.concat(frames).drop_duplicates(subset="date", keep="last")
            .sort_values("date").reset_index(drop=True))
    if start and not df.empty:
        df = df[df["date"] >= pd.Timestamp(start).date()].reset_index(drop=True)
    return df


def _parse(text: str, tenor: str) -> pd.DataFrame:
    """Pure: one MOF curve CSV → [date, value] for one tenor."""
    lines = text.splitlines()
    head = next((i for i, line in enumerate(lines) if line.startswith("Date,")), None)
    if head is None:
        raise ValueError("MOF JGB file: no 'Date,' header line (layout changed?)")
    # Only the header and dated rows: the note lines at the end carry their
    # own commas and would break the column count.
    body = [lines[head]] + [ln for ln in lines[head + 1:] if re.match(r"\d{4}/\d{1,2}/\d{1,2},", ln)]
    raw = pd.read_csv(io.StringIO("\n".join(body)), dtype=str)
    if tenor not in raw.columns:
        raise ValueError(f"MOF JGB file: no {tenor!r} column in {list(raw.columns)[1:]}")
    df = pd.DataFrame({"date": pd.to_datetime(raw["Date"], format="%Y/%m/%d", errors="coerce"),
                       "value": pd.to_numeric(raw[tenor], errors="coerce")})
    df = df.dropna().sort_values("date").reset_index(drop=True)
    df["date"] = df["date"].dt.date
    return df
