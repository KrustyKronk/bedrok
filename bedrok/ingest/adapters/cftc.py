"""CFTC Commitments of Traders adapter (no API key).

Weekly speculative positioning from the legacy futures-only report. Bulk by
design: one annual zip serves every market, so fetching per series would
re-download the same file five times.

Why the LEGACY report and not the disaggregated one: the disaggregated report
covers physical commodities only — equity, rates and FX futures live in the
separate Traders in Financial Futures report — so legacy is the only variant
that covers all five markets in one format.

Two traps, both confirmed against the live archive:
  * Market codes are whitespace-padded in the older files ("'088691 '"), so
    they must be stripped before matching.
  * Codes are REUSED. 099741 was the European Currency Unit in 1986 and became
    EURO FX in 1999 — different instruments, twelve years apart. Each market
    therefore declares the year its current contract began, and earlier rows
    under the same code are ignored rather than spliced into a fake history.

Match on code, never on name: markets get renamed in place (the 10-year note
went from "6.5-10 YEAR U.S. TREASURY NOTE" to "UST 10Y NOTE"), and names also
collide — NYMEX WTI is 067651 while ICE Europe's look-alike is 067411.

Licence: US government work, public domain.
"""
from __future__ import annotations

import csv
import io
import time
import zipfile

import pandas as pd
import requests

from bedrok.ingest.adapters._util import BROWSER_UA

BASE = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"

# series_id → the contract, and the year ITS contract started (see the code-reuse
# note above; these were established by probing the archive, not assumed).
MARKETS: dict[str, dict] = {
    "cot_es_net_noncomm": {
        "code": "13874A", "start_year": 1997,
        "name": "CFTC net non-commercial positioning — E-mini S&P 500"},
    "cot_ust10y_net_noncomm": {
        "code": "043602", "start_year": 1986,
        "name": "CFTC net non-commercial positioning — 10y US Treasury note"},
    "cot_wti_net_noncomm": {
        "code": "067651", "start_year": 1986,
        "name": "CFTC net non-commercial positioning — WTI crude (NYMEX)"},
    "cot_gold_net_noncomm": {
        "code": "088691", "start_year": 1986,
        "name": "CFTC net non-commercial positioning — gold (COMEX)"},
    "cot_eur_net_noncomm": {
        "code": "099741", "start_year": 1999,
        "name": "CFTC net non-commercial positioning — euro FX"},
}

EARLIEST_YEAR = 1986

_CODE = "CFTC Contract Market Code"
_DATE = "As of Date in Form YYYY-MM-DD"
_LONG = "Noncommercial Positions-Long (All)"
_SHORT = "Noncommercial Positions-Short (All)"


def _parse_zip(content: bytes) -> tuple[list[str], list[list[str]]]:
    """Annual zip → (header, rows). Pure: no network, no filtering."""
    z = zipfile.ZipFile(io.BytesIO(content))
    text = z.read(z.namelist()[0]).decode("utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    return (rows[0], rows[1:]) if rows else ([], [])


def _extract(header: list[str], rows: list[list[str]],
             year: int | None = None) -> dict[str, pd.DataFrame]:
    """Rows → {series_id: DataFrame[date, value]} where value is net
    non-commercial positioning (long minus short), in contracts."""
    if not header:
        return {}
    ci = {name: i for i, name in enumerate(header)}
    if not {_CODE, _DATE, _LONG, _SHORT} <= set(ci):
        return {}
    by_code = {spec["code"]: (sid, spec) for sid, spec in MARKETS.items()}

    acc: dict[str, list] = {}
    for r in rows:
        code = r[ci[_CODE]].strip()
        hit = by_code.get(code)
        if hit is None:
            continue
        sid, spec = hit
        date = pd.to_datetime(r[ci[_DATE]].strip(), errors="coerce")
        if pd.isna(date) or date.year < spec["start_year"]:
            continue                      # pre-dates THIS contract — see module docstring
        try:
            net = int(r[ci[_LONG]].strip() or 0) - int(r[ci[_SHORT]].strip() or 0)
        except ValueError:
            continue
        acc.setdefault(sid, []).append((date.date(), float(net)))

    out = {}
    for sid, data in acc.items():
        df = (pd.DataFrame(data, columns=["date", "value"])
                .drop_duplicates(subset="date", keep="last")
                .sort_values("date").reset_index(drop=True))
        if not df.empty:
            out[sid] = df
    return out


def fetch_cot(years: list[int]) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Download the given annual files and merge them per series.

    Returns (per_series, missing_years). A year the archive does not carry (the
    current file does not exist until the first report of the year) is reported
    rather than raised — the rest of the history is still worth having."""
    frames: dict[str, list[pd.DataFrame]] = {}
    missing: list[str] = []
    for i, year in enumerate(years):
        if i:
            time.sleep(0.3)   # a deep backfill is 40 files — don't hammer cftc.gov
        r = requests.get(BASE.format(year=year), headers=BROWSER_UA, timeout=240)
        if r.status_code == 404:
            missing.append(str(year))
            continue
        r.raise_for_status()
        header, rows = _parse_zip(r.content)
        for sid, df in _extract(header, rows, year).items():
            frames.setdefault(sid, []).append(df)

    per_series = {}
    for sid, parts in frames.items():
        df = (pd.concat(parts, ignore_index=True)
                .drop_duplicates(subset="date", keep="last")
                .sort_values("date").reset_index(drop=True))
        per_series[sid] = df
    return per_series, missing
