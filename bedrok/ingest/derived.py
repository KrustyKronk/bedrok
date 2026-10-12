"""Series Bedrok computes rather than fetches.

Bedrok's value is not the raw feeds — those are free at source — but what sits
on top of them. These are the cheapest form of that: numbers a reader would
otherwise have to derive by hand every time, computed once and stored with the
data.

Kept deliberately small and honest. A derived series must be:
  * arithmetic anyone would agree with (a spread is a subtraction), not a
    modelled opinion — opinions belong in the reasoning layer, not the store;
  * reproducible from series already in the store, so a user can check it;
  * labelled with its inputs in `source_code`, so it is never mistaken for
    something a statistical agency published.

Recomputed in full on every run rather than incrementally. The maths is a few
DuckDB queries over monthly series — milliseconds — and total recomputation
means no window bookkeeping and no way to drift out of sync with revised inputs.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from bedrok.core import licensing, store
from bedrok.ingest import transforms
from bedrok.ingest.adapters import nber

# Since session 3b the US spreads, breakevens and the recession flag are here
# rather than fetched from FRED: FRED computes them by the same arithmetic from
# the same Fed Board inputs, and its terms forbid storing what it serves. Every
# input must be a stored (cache = store) series — a derived row built from a
# session-only input would put that input's content in the store by the back
# door. tests/test_derived.py pins that.
#
# Deliberately NOT here:
#   us_real_10y_proxy (us_10y - us_breakeven_10y) — DFII10 ships as us_10y_real,
#     the actual traded TIPS yield, so a proxy would only invite the question of
#     why they disagree.
#   uk_de_10y_spread — uk_gilt_10y is daily and ltgov_yield_de is a monthly
#     average; differencing across frequencies would look precise and be sloppy.
#   COT net positioning — cftc.py already nets long minus short at source.

# Every sovereign spread carries this. The inputs start in 1980, but a spread
# to the Bund only means "fragmentation risk inside a shared currency" from
# 1999. Before that these countries had their own currencies, and the gap is
# mostly an inflation and devaluation-risk differential: Italy's widest print
# in the whole series is May 1982 at 12.0 points, comfortably above its 2011-12
# euro-crisis peak. Read pre-1999 as a different quantity, not a worse crisis.
_SPREAD_ERA = ("Pre-1999 values are NOT comparable: before monetary union this "
               "is a currency and inflation differential rather than a "
               "fragmentation signal — the 1980s prints exceed the 2011-12 "
               "euro-crisis peak and do not mean what they appear to mean.")

DERIVED: list[dict] = [
    {"series_id": "it_de_10y_spread", "op": "diff",
     "inputs": ["ltgov_yield_it", "ltgov_yield_de"],
     "name": "Italy–Germany 10y sovereign spread (BTP-Bund)",
     "frequency": "monthly", "units": "percentage points",
     "notes": "THE euro-area fragmentation gauge; monthly averages so it will "
              "not match a daily BTP-Bund print exactly. " + _SPREAD_ERA},
    {"series_id": "fr_de_10y_spread", "op": "diff",
     "inputs": ["ltgov_yield_fr", "ltgov_yield_de"],
     "name": "France–Germany 10y sovereign spread (OAT-Bund)",
     "frequency": "monthly", "units": "percentage points",
     "notes": "core-periphery stress measured at the core. " + _SPREAD_ERA},
    {"series_id": "es_de_10y_spread", "op": "diff",
     "inputs": ["ltgov_yield_es", "ltgov_yield_de"],
     "name": "Spain–Germany 10y sovereign spread (Bono-Bund)",
     "frequency": "monthly", "units": "percentage points",
     "notes": "peripheral spread; compare with it_de_10y_spread to separate "
              "country risk from broad euro-area risk. " + _SPREAD_ERA},
    {"series_id": "pt_de_10y_spread", "op": "diff",
     "inputs": ["ltgov_yield_pt", "ltgov_yield_de"],
     "name": "Portugal–Germany 10y sovereign spread",
     "frequency": "monthly", "units": "percentage points",
     "notes": "the periphery's post-2012 re-rating shows up here first. "
              + _SPREAD_ERA},
    {"series_id": "us_cpi_3m_annualized", "op": "mom3_ann",
     "inputs": ["us_cpi"],
     "name": "US CPI 3-month annualised rate",
     "frequency": "monthly", "units": "percent",
     "notes": "inflation MOMENTUM: what the last quarter implies if sustained. "
              "Turns months before the year-over-year rate does — that is the "
              "point of it, and also why it is noisier"},
    {"series_id": "ea_hicp_3m_annualized", "op": "mom3_ann",
     "inputs": ["ea_hicp_index"],
     "name": "Euro area HICP 3-month annualised rate",
     "frequency": "monthly", "units": "percent",
     "notes": "the euro-area counterpart to us_cpi_3m_annualized"},
    {"series_id": "us_10y2y", "op": "diff", "inputs": ["us_10y", "us_2y"],
     "name": "US 10y minus 2y term spread", "frequency": "daily",
     "units": "percentage points",
     "notes": "the classic curve-inversion gauge; FRED's T10Y2Y is the same "
              "subtraction"},
    {"series_id": "us_10y3m", "op": "diff", "inputs": ["us_10y", "us_3m"],
     "name": "US 10y minus 3m term spread", "frequency": "daily",
     "units": "percentage points",
     "notes": "the spread the recession-probability literature uses; FRED's "
              "T10Y3M is the same subtraction"},
    {"series_id": "us_breakeven_10y", "op": "diff", "inputs": ["us_10y", "us_10y_real"],
     "name": "US 10-year breakeven inflation rate", "frequency": "daily",
     "units": "percent",
     "notes": "nominal minus TIPS yield: market-implied average inflation over "
              "ten years, including an inflation risk premium. FRED's T10YIE "
              "is the same subtraction"},
    {"series_id": "us_breakeven_5y", "op": "diff", "inputs": ["us_5y", "us_5y_real"],
     "name": "US 5-year breakeven inflation rate", "frequency": "daily",
     "units": "percent",
     "notes": "nominal minus TIPS yield over five years; FRED's T5YIE is the "
              "same subtraction"},
    {"series_id": "us_5y5y_fwd_inflation", "op": "fwd5y5y",
     "inputs": ["us_breakeven_10y", "us_breakeven_5y"],
     "name": "US 5-year, 5-year forward inflation expectation rate",
     "frequency": "daily", "units": "percent",
     "notes": "expected average inflation over the five years starting five "
              "years out — the Fed's preferred read of anchoring. FRED's "
              "T5YIFR formula: ((1+b10)^10 / (1+b5)^5)^(1/5) - 1"},
    {"series_id": "us_recession", "op": "nber_recession", "inputs": [],
     "name": "US recession indicator (NBER)", "frequency": "monthly",
     "units": "binary 0 or 1",
     "notes": "1 from the month after an NBER peak through the trough, as "
              "FRED's USREC. The NBER dates turning points a year or more "
              "after the fact, so the latest months read 0 until it does",
     "staleness_days": 60, "license": licensing.ATTRIBUTION},
]

# Net trade per geography (session C): exports plus imports, because Eurostat
# publishes the imports contribution already negative. The geographies are the
# GDP breakdown families' own (pipeline.family_geos on gdp_contrib_*; a test
# pins the match, since pipeline cannot be imported here without a cycle).
GDP_NET_GEOS = ["ea21", "eu27_2020", "de", "fr", "it", "es", "nl", "be", "at", "pt",
                "ie", "el", "fi"]
GDP_NET_GEOS_Q = [g for g in GDP_NET_GEOS if g not in ("de", "nl")]
_NET_NOTE = ("imports are published as a negative contribution, so net trade is "
             "exports plus imports. One of the parts that add up to GDP growth")
DERIVED += [
    {"series_id": f"gdp_contrib_net_{g}", "op": "sum",
     "inputs": [f"gdp_contrib_exp_{g}", f"gdp_contrib_imp_{g}"],
     "name": f"Contribution of net trade to annual GDP growth — {g.upper()}",
     "frequency": "annual", "units": "percentage points", "staleness_days": 800,
     "notes": _NET_NOTE + f" (gdp_annual_{g})"}
    for g in GDP_NET_GEOS
] + [
    {"series_id": f"gdp_contrib_q_net_{g}", "op": "sum",
     "inputs": [f"gdp_contrib_q_exp_{g}", f"gdp_contrib_q_imp_{g}"],
     "name": f"Contribution of net trade to GDP growth on a year earlier — {g.upper()}",
     "frequency": "quarterly", "units": "percentage points", "staleness_days": 260,
     "notes": _NET_NOTE + f" (gdp_yoy_{g})"}
    for g in GDP_NET_GEOS_Q
]


def _series(con, series_id: str) -> pd.DataFrame:
    return con.execute(
        "SELECT date, value FROM macro_series WHERE series_id = ? ORDER BY date",
        [series_id]).fetch_df()


def _diff(con, inputs: list[str]) -> pd.DataFrame:
    a, b = (_series(con, s) for s in inputs)
    if a.empty or b.empty:
        return pd.DataFrame(columns=["date", "value"])
    out = a.merge(b, on="date", suffixes=("_a", "_b"))
    out["value"] = out["value_a"] - out["value_b"]
    return out[["date", "value"]]


def _sum(con, inputs: list[str]) -> pd.DataFrame:
    a, b = (_series(con, s) for s in inputs)
    if a.empty or b.empty:
        return pd.DataFrame(columns=["date", "value"])
    out = a.merge(b, on="date", suffixes=("_a", "_b"))
    out["value"] = out["value_a"] + out["value_b"]
    return out[["date", "value"]]


def _mom3_ann(con, inputs: list[str]) -> pd.DataFrame:
    """3-month change on an index level, annualised: ((x_t/x_t-3)^4 - 1) * 100.
    The arithmetic is transforms.mom3_ann, shared with the MCP server."""
    df = _series(con, inputs[0])
    if len(df) < 4:
        return pd.DataFrame(columns=["date", "value"])
    return transforms.mom3_ann(df)


def _fwd5y5y(con, inputs: list[str]) -> pd.DataFrame:
    """FRED's T5YIFR: ((1+b10/100)^10 / (1+b5/100)^5)^(1/5) - 1, in percent."""
    a, b = (_series(con, s) for s in inputs)
    if a.empty or b.empty:
        return pd.DataFrame(columns=["date", "value"])
    out = a.merge(b, on="date", suffixes=("_10", "_5"))
    out["value"] = (((1 + out["value_10"] / 100) ** 10
                     / (1 + out["value_5"] / 100) ** 5) ** (1 / 5) - 1) * 100
    return out[["date", "value"]]


def _nber_recession(con, inputs: list[str]) -> pd.DataFrame:
    today = date.today()
    return nber.recession_months(nber.fetch_cycles(), date(today.year, today.month, 1))


OPS = {"diff": _diff, "sum": _sum, "mom3_ann": _mom3_ann, "fwd5y5y": _fwd5y5y,
       "nber_recession": _nber_recession}


def catalog_row(spec: dict) -> dict:
    """The catalog row of a derived series — known before it is ever computed,
    so a fresh store can find it (pipeline.load_catalog registers these)."""
    sources = " and ".join(spec["inputs"]) or "NBER business-cycle dates"
    return {
        "series_id": spec["series_id"], "source": "derived",
        "source_code": f"{spec['op']}:{'-'.join(spec['inputs'])}",
        "name": spec["name"], "frequency": spec["frequency"], "units": spec["units"],
        "notes": f"COMPUTED BY BEDROK from {sources}. " + spec["notes"],
        "staleness_days": spec.get("staleness_days"),
        "license": spec.get("license", licensing.ATTRIBUTION),
        "cache": licensing.STORE,
    }


def origin(spec: dict) -> str:
    """The filled_from value a derived series' stored history carries."""
    return f"derived:{spec['op']}:{'-'.join(spec['inputs'])}"


def sync_derived(con, incremental: bool = False) -> list[tuple]:
    """Recompute every derived series. `incremental` is accepted and ignored —
    see the module docstring."""
    report, cat_rows = [], []
    for spec in DERIVED:
        sid, op = spec["series_id"], spec["op"]
        missing = [s for s in spec["inputs"]
                   if con.execute("SELECT count(*) FROM macro_series WHERE series_id = ?",
                                  [s]).fetchone()[0] == 0]
        if missing:
            # An input that has not synced yet is a sequencing fact, not a fault:
            # the next run picks it up once the upstream stage has landed.
            report.append((sid, "skip", f"awaiting input: {', '.join(missing)}"))
            continue
        try:
            df = OPS[op](con, spec["inputs"])
        except Exception as e:  # noqa: BLE001
            report.append((sid, "FAIL", f"{type(e).__name__}: {str(e)[:60]}"))
            continue
        if df.empty:
            report.append((sid, "skip", "inputs do not overlap"))
            continue
        # Arithmetic on decimals leaves binary noise (5.22 - 2.87 is stored as
        # 2.3499999999999996); ten places is far below any published precision.
        df = df.assign(value=df["value"].astype(float).round(10))
        # Recomputed in full, so the whole history is replaced: that also
        # clears whatever the id held before it was derived (FRED's copy).
        n = store.replace_macro_series(con, sid, df, origin(spec))
        cat_rows.append(catalog_row(spec))
        report.append((sid, "ok", f"{n} rows"))
    if cat_rows:
        store.upsert_macro_catalog(con, pd.DataFrame(cat_rows))
    return report
