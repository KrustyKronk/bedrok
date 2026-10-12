"""cache = session: fetched for a conversation, never written to the store."""
import duckdb
import pandas as pd
import pytest

from bedrok.core import config, licensing, store
from bedrok.engines import pctile
from bedrok.ingest import live, pipeline


def _frame(dates, values):
    return pd.DataFrame({"date": pd.to_datetime(dates).date, "value": values})


@pytest.fixture
def con(monkeypatch):
    monkeypatch.setenv("BEDROK_PACE_FRED", "0")
    c = duckdb.connect(":memory:")
    store.init_db(c)
    store.upsert_macro_catalog(c, pd.DataFrame([
        {"series_id": "us_vix", "source": "fred", "source_code": "VIXCLS", "name": "VIX",
         "license": licensing.FRED_CHANNEL, "cache": "session"},
        {"series_id": "us_10y", "source": "fred", "source_code": "DGS10", "name": "10y",
         "license": licensing.FRED_CHANNEL, "cache": "store"}]))
    return c


def test_sync_never_fetches_or_writes_a_session_row(con, monkeypatch):
    fetched = []
    monkeypatch.setitem(pipeline.MACRO_FETCHERS, "fred",
                        lambda code, start: fetched.append(code) or _frame(["2026-10-01"], [1.0]))
    pipeline.sync_macro(con)
    assert fetched == ["DGS10"]
    assert con.execute("SELECT count(*) FROM macro_series WHERE series_id = 'us_vix'"
                       ).fetchone()[0] == 0


def test_a_row_that_becomes_session_has_its_history_deleted(con, monkeypatch):
    """A real store held FRED's VIX until session 3b."""
    store.replace_macro_series(con, "us_vix", _frame(["2026-10-01", "2026-10-02"], [15.0, 16.0]),
                               "fred:VIXCLS")
    monkeypatch.setitem(pipeline.MACRO_FETCHERS, "fred", lambda code, start: _frame([], []))
    report = pipeline.sync_macro(con, incremental=True, only={"us_vix"})
    assert report == [("us_vix", "ok", "session-only: 2 stored rows removed")]
    assert con.execute("SELECT count(*) FROM macro_series WHERE series_id = 'us_vix'"
                       ).fetchone()[0] == 0
    assert con.execute("SELECT filled_from FROM macro_catalog WHERE series_id = 'us_vix'"
                       ).fetchone()[0] is None
    # and it says nothing on later runs
    assert pipeline.sync_macro(con, incremental=True, only={"us_vix"}) == []


def test_live_fetch_returns_the_series_without_storing_it(con, monkeypatch):
    monkeypatch.setitem(pipeline.MACRO_FETCHERS, "fred",
                        lambda code, start: _frame(["2026-10-01", "2026-10-02"], [15.0, 20.0]))
    df = live.fetch(con, "us_vix")
    assert len(df) == 2
    r = pctile.compute(con, "us_vix", frame=df)
    assert r["n"] == 2 and r["value"] == 20.0 and r["pctile"] == 100.0
    assert con.execute("SELECT count(*) FROM macro_series").fetchone()[0] == 0


def test_live_fetch_refuses_a_stored_series(con):
    with pytest.raises(ValueError, match="not a session-only"):
        live.fetch(con, "us_10y")


def test_live_fetch_without_a_key_is_a_missing_key(con, monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "")
    with pytest.raises(live.MissingKey):
        live.fetch(con, "us_vix")


def test_runtime_catalog_rows_default_to_store():
    c = duckdb.connect(":memory:")
    store.init_db(c)
    c.execute("INSERT INTO macro_catalog (series_id, source, source_code, name) "
              "VALUES ('wb_gold', 'worldbank', 'x', 'gold')")
    store.init_db(c)
    assert c.execute("SELECT cache FROM macro_catalog").fetchone()[0] == "store"
