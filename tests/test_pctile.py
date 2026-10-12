"""pctile.compute: historical rank always carries its sample size + span."""
import datetime as dt

import duckdb
import pandas as pd

from bedrok.core import store
from bedrok.engines import pctile


def _series(con, sid, dates, values):
    store.upsert_macro_series(con, sid, pd.DataFrame(
        {"date": [d.date() if isinstance(d, dt.datetime) else d for d in dates],
         "value": values}))


def test_exact_percentile_on_known_values():
    con = duckdb.connect(":memory:")
    store.init_db(con)
    # values 1..10 on ten consecutive days
    days = pd.date_range("2024-01-01", periods=10, freq="D")
    _series(con, "s", days, list(range(1, 11)))
    # rank the value 7: seven of ten obs (1..7) are <= 7 → 70th percentile
    r = pctile.compute(con, "s", value=7.0)
    assert r["n"] == 10
    assert abs(r["pctile"] - 70.0) < 1e-9
    # latest defaults to the last obs (10) → 100th percentile
    r2 = pctile.compute(con, "s")
    assert r2["is_latest"] and abs(r2["pctile"] - 100.0) < 1e-9


def test_reports_sample_span_and_short_flag():
    con = duckdb.connect(":memory:")
    store.init_db(con)
    days = pd.date_range("2024-01-01", periods=200, freq="D")  # ~0.55y
    _series(con, "short", days, list(range(200)))
    r = pctile.compute(con, "short", value=100.0)
    assert r["d0"] == dt.date(2024, 1, 1)
    assert r["span_years"] < 5  # triggers the short-sample caveat in main()


def test_since_narrows_the_sample():
    con = duckdb.connect(":memory:")
    store.init_db(con)
    days = pd.date_range("2020-01-01", periods=1000, freq="D")
    _series(con, "s", days, list(range(1000)))
    full = pctile.compute(con, "s", value=500.0)
    recent = pctile.compute(con, "s", value=500.0, since="2022-01-01")
    assert recent["n"] < full["n"]
    assert recent["d0"] >= dt.date(2022, 1, 1)


def test_missing_series_returns_none():
    con = duckdb.connect(":memory:")
    store.init_db(con)
    assert pctile.compute(con, "does_not_exist") is None
