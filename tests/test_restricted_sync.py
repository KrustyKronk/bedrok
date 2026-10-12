"""Restricted series: fetched by sync_macro into the one store with the user's
own key, skipped cleanly without one."""
import duckdb
import pandas as pd
import pytest

from bedrok.core import config, licensing, store
from bedrok.ingest import pipeline


@pytest.fixture(autouse=True)
def _no_pace(monkeypatch):
    monkeypatch.setenv("BEDROK_PACE_FRED", "0")


def _store_with_restricted():
    con = duckdb.connect(":memory:")
    store.init_db(con)
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "us_hy_oas", "source": "fred", "source_code": "BAMLH0A0HYM2",
         "name": "US HY OAS", "license": licensing.RESTRICTED},
        {"series_id": "us_cpi", "source": "fred", "source_code": "CPIAUCSL",
         "name": "US CPI", "license": licensing.FRED_CHANNEL},
    ]))
    return con


def test_restricted_series_is_fetched_into_macro_series_with_a_key(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    fetched = []

    def fake_fred(code, start):
        fetched.append(code)
        return pd.DataFrame({"date": pd.to_datetime(["2024-01-02", "2024-01-03"]).date,
                             "value": [3.1, 3.2]})
    monkeypatch.setitem(pipeline.MACRO_FETCHERS, "fred", fake_fred)

    con = _store_with_restricted()
    report = pipeline.sync_macro(con)
    assert "BAMLH0A0HYM2" in fetched
    assert ("us_hy_oas", "ok") in [r[:2] for r in report]
    assert con.execute("SELECT count(*) FROM macro_series "
                       "WHERE series_id = 'us_hy_oas'").fetchone()[0] == 2
    # there is no separate personal layer any more
    assert not con.execute("SELECT count(*) FROM duckdb_tables() "
                           "WHERE table_name = 'macro_series_local'").fetchone()[0]


def test_restricted_series_skips_cleanly_without_a_key(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "")
    from bedrok.ingest.adapters import fred
    monkeypatch.setattr(fred, "fetch",
                        lambda *a, **k: pytest.fail("no network call without a key"))
    con = _store_with_restricted()
    report = pipeline.sync_macro(con, only={"us_hy_oas"})
    assert report == [("us_hy_oas", "skip", "no FRED_API_KEY (keyless mode)")]
    assert con.execute("SELECT count(*) FROM macro_series").fetchone()[0] == 0
    # a skip is not a FAIL, so a keyless refresh never goes red
    assert pipeline.print_report("x", report) == 0


def test_incremental_start_uses_the_last_stored_date(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    con = _store_with_restricted()
    store.upsert_macro_series(con, "us_hy_oas", pd.DataFrame(
        {"date": pd.to_datetime(["2024-06-01"]).date, "value": [4.0]}))
    con.execute("UPDATE macro_catalog SET filled_from = 'fred:BAMLH0A0HYM2' "
                "WHERE series_id = 'us_hy_oas'")

    seen = {}

    def fake_fred(code, start):
        seen["start"] = start
        return pd.DataFrame({"date": pd.to_datetime(["2024-06-10"]).date, "value": [4.1]})
    monkeypatch.setitem(pipeline.MACRO_FETCHERS, "fred", fake_fred)

    pipeline.sync_macro(con, incremental=True, only={"us_hy_oas"})
    # OVERLAP_DAYS (7) before the last stored date
    assert seen["start"] == "2024-05-25"


def test_incremental_start_never_lies_in_the_future():
    """A forecast series stores observations dated after today. Its incremental
    start must be capped at today minus the overlap, or the ECB answers HTTP 400
    and the series silently stops updating (it did, every week, in the old pack
    build)."""
    import datetime as dt
    con = duckdb.connect(":memory:")
    store.init_db(con)
    future = dt.date.today() + dt.timedelta(days=500)
    store.upsert_macro_series(con, "ea_spf_infl_1y", pd.DataFrame(
        {"date": [future], "value": [2.0]}))
    start = pipeline._incremental_start(con, "macro_series", "series_id", "ea_spf_infl_1y")
    assert start == (dt.date.today() - dt.timedelta(days=pipeline.OVERLAP_DAYS)).isoformat()
