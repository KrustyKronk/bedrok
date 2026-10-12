"""A series re-pointed to a new source has its history replaced, never topped
up — so two publishers' levels are never stitched together (CLAUDE.md rule 6)."""
import duckdb
import pandas as pd
import pytest

from bedrok.core import config, store
from bedrok.ingest import pipeline


@pytest.fixture(autouse=True)
def _no_pace(monkeypatch):
    for src in ("FRED", "BLS", "BEA"):
        monkeypatch.setenv(f"BEDROK_PACE_{src}", "0")


def _frame(dates, values):
    return pd.DataFrame({"date": pd.to_datetime(dates).date, "value": values})


def _store(source="fred", code="CPIAUCSL"):
    """A store whose us_cpi history came from FRED, as every store did before
    session 3."""
    con = duckdb.connect(":memory:")
    store.init_db(con)
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "us_cpi", "source": "fred", "source_code": "CPIAUCSL",
         "name": "US CPI", "license": "public_domain"}]))
    store.replace_macro_series(con, "us_cpi",
                               _frame(["1950-01-01", "2024-01-01", "2024-02-01"],
                                      [1.0, 2.0, 3.0]), "fred:CPIAUCSL")
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "us_cpi", "source": source, "source_code": code,
         "name": "US CPI", "license": "public_domain"}]))
    return con


def _values(con):
    return con.execute("SELECT date, value FROM macro_series WHERE series_id = 'us_cpi' "
                       "ORDER BY date").fetchall()


def test_repointed_series_is_pulled_in_full_and_replaced(monkeypatch):
    seen = {}

    def fake_bls(code, start):
        seen["start"] = start
        return _frame(["2024-01-01", "2024-02-01"], [2.5, 3.5])
    monkeypatch.setitem(pipeline.MACRO_FETCHERS, "bls", fake_bls)

    con = _store("bls", "CUSR0000SA0")
    report = pipeline.sync_macro(con, incremental=True)
    assert seen["start"] is None                       # full pull, not a top-up
    assert [str(d) for d, _ in _values(con)] == ["2024-01-01", "2024-02-01"]  # 1950 FRED point gone
    assert report[0][2] == "2 rows (replaced fred:CPIAUCSL)"
    assert con.execute("SELECT filled_from FROM macro_catalog").fetchone()[0] == \
        "bls:CUSR0000SA0"

    # the next run is an ordinary incremental top-up
    pipeline.sync_macro(con, incremental=True)
    assert seen["start"] is not None


def test_a_failed_new_source_keeps_the_old_history(monkeypatch):
    def broken(code, start):
        raise RuntimeError("endpoint moved")
    monkeypatch.setitem(pipeline.MACRO_FETCHERS, "bls", broken)
    con = _store("bls", "CUSR0000SA0")
    report = pipeline.sync_macro(con, incremental=True)
    assert report[0][1] == "FAIL"
    assert len(_values(con)) == 3
    assert con.execute("SELECT filled_from FROM macro_catalog").fetchone()[0] == "fred:CPIAUCSL"


def test_an_empty_answer_from_the_new_source_keeps_the_old_history(monkeypatch):
    monkeypatch.setitem(pipeline.MACRO_FETCHERS, "bls",
                        lambda code, start: _frame([], []))
    con = _store("bls", "CUSR0000SA0")
    assert pipeline.sync_macro(con)[0][1] == "FAIL"
    assert len(_values(con)) == 3


def test_a_missing_key_keeps_the_old_history(monkeypatch):
    monkeypatch.setenv("BEA_API_KEY", "")
    con = _store("bea", "T20804:1:M")
    report = pipeline.sync_macro(con, incremental=True)
    assert report == [("us_cpi", "skip", "no BEA_API_KEY (keyless mode)")]
    assert len(_values(con)) == 3


def test_migration_credits_existing_history_to_the_current_source():
    """A store from before filled_from existed must not treat every series as
    re-pointed: what it holds came from the source its catalog names."""
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE macro_catalog (series_id TEXT PRIMARY KEY,
        source TEXT NOT NULL, source_code TEXT NOT NULL, name TEXT NOT NULL,
        frequency TEXT, units TEXT, notes TEXT, staleness_days INTEGER, license TEXT)""")
    con.execute("INSERT INTO macro_catalog (series_id, source, source_code, name) VALUES "
                "('us_cpi', 'fred', 'CPIAUCSL', 'US CPI'), ('empty', 'ecb', 'X', 'never synced')")
    store.init_db(con)
    store.upsert_macro_series(con, "us_cpi", _frame(["2024-01-01"], [1.0]))
    # init_db again is idempotent and must not overwrite
    con.execute("ALTER TABLE macro_catalog DROP COLUMN filled_from")
    store.init_db(con)
    got = dict(con.execute("SELECT series_id, filled_from FROM macro_catalog").fetchall())
    assert got == {"us_cpi": "fred:CPIAUCSL", "empty": None}


def test_a_catalog_reload_never_touches_filled_from():
    con = _store()
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "us_cpi", "source": "fred", "source_code": "CPIAUCSL",
         "name": "renamed", "license": "public_domain"}]))
    assert con.execute("SELECT filled_from FROM macro_catalog").fetchone()[0] == "fred:CPIAUCSL"
