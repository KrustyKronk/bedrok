"""Store: schema creation + idempotent upserts on a throwaway DB."""
import duckdb
import pandas as pd

from bedrok.core import store


def _con():
    con = duckdb.connect(":memory:")
    store.init_db(con)
    return con


def test_schema_is_three_tables_and_one_view():
    con = _con()
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE NOT internal").fetchall()}
    assert tables == {"macro_catalog", "macro_series", "fx_rates"}
    views = {r[0] for r in con.execute(
        "SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()}
    assert views == {"all_series"}
    # empty store → empty view, but the view exists
    assert con.execute("SELECT count(*) FROM all_series").fetchone()[0] == 0


def test_catalog_license_migrates_onto_a_preexisting_store():
    """A store created before the licence column must gain it on reconnect —
    stores are long-lived and upgraded in place, never rebuilt by the code."""
    con = duckdb.connect(":memory:")
    # the pre-migration shape: original columns only
    con.execute("""CREATE TABLE macro_catalog (
        series_id TEXT PRIMARY KEY, source TEXT NOT NULL, source_code TEXT NOT NULL,
        name TEXT NOT NULL, frequency TEXT, units TEXT, notes TEXT)""")
    con.execute("INSERT INTO macro_catalog VALUES "
                "('us_cpi','fred','CPIAUCSL','US CPI','monthly','index','')")
    store.init_db(con)          # runs the migrations
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='macro_catalog'").fetchall()}
    assert {"staleness_days", "license"} <= cols
    # the pre-existing row survives, with a NULL licence for validate to flag
    assert con.execute(
        "SELECT license FROM macro_catalog WHERE series_id='us_cpi'").fetchone()[0] is None


def test_init_db_repoints_a_pre_session_2_all_series_view():
    """A store made while prices lived here has an all_series view that also
    unions prices_eod. init_db must replace it, or dropping the old price table
    later would break every read of the view."""
    con = duckdb.connect(":memory:")
    con.execute(store.SCHEMA)
    con.execute("CREATE TABLE prices_eod (ticker TEXT, date DATE, close DOUBLE, "
                "adj_close DOUBLE)")
    con.execute("INSERT INTO prices_eod VALUES ('SPY', DATE '2026-01-02', 1.0, 1.0)")
    con.execute("""CREATE VIEW all_series AS
        SELECT 'macro' AS kind, series_id, date, value FROM macro_series
        UNION ALL SELECT 'price', ticker, date, coalesce(adj_close, close) FROM prices_eod
        UNION ALL SELECT 'fx', pair, date, rate FROM fx_rates""")
    store.init_db(con)
    kinds = {r[0] for r in con.execute("SELECT DISTINCT kind FROM all_series").fetchall()}
    assert "price" not in kinds
    con.execute("DROP TABLE prices_eod")
    assert con.execute("SELECT count(*) FROM all_series").fetchone()[0] == 0


def test_upsert_idempotent():
    con = _con()
    df = pd.DataFrame({"date": pd.to_datetime(["2024-01-01", "2024-01-02"]).date,
                       "value": [1.0, 2.0]})
    n1 = store.upsert_macro_series(con, "test_series", df)
    n2 = store.upsert_macro_series(con, "test_series", df)
    assert n1 == n2 == 2
    assert con.execute("SELECT count(*) FROM macro_series").fetchone()[0] == 2
    # value update on conflict, not duplication
    df2 = df.assign(value=[9.0, 9.0])
    store.upsert_macro_series(con, "test_series", df2)
    assert con.execute("SELECT count(*) FROM macro_series").fetchone()[0] == 2
    assert con.execute(
        "SELECT max(value) FROM macro_series").fetchone()[0] == 9.0
