"""validate: the licence and staleness checks over the one store."""
import duckdb
import pandas as pd

from bedrok.core import licensing, store
from bedrok.ops import validate


def _store():
    con = duckdb.connect(":memory:")
    store.init_db(con)
    return con


def test_restricted_rows_held_locally_are_info_never_a_violation(capsys):
    """There is no pack to violate: a restricted series fetched with your own key
    into your own store is reported, not counted."""
    con = _store()
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "us_hy_oas", "source": "fred", "source_code": "BAMLH0A0HYM2",
         "name": "HY OAS", "license": licensing.RESTRICTED}]))
    store.upsert_macro_series(con, "us_hy_oas", pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-02"]).date, "value": [3.1]}))
    assert validate.license_check(con) == 0
    out = capsys.readouterr().out
    assert "info" in out and "us_hy_oas" in out
    assert "LICENSE-VIOLATION" not in out


def test_a_session_series_with_stored_rows_is_a_finding(capsys):
    con = _store()
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "us_vix", "source": "fred", "source_code": "VIXCLS",
         "name": "VIX", "license": licensing.FRED_CHANNEL, "cache": "session"}]))
    assert validate.license_check(con) == 0
    store.upsert_macro_series(con, "us_vix", pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-02"]).date, "value": [15.0]}))
    assert validate.license_check(con) == 1
    assert "SESSION-STORED" in capsys.readouterr().out


def test_fx_staleness_fires_at_twenty_days_not_at_two(capsys):
    con = _store()
    store.upsert_fx(con, "EURUSD", pd.DataFrame(
        {"date": [(pd.Timestamp.today() - pd.Timedelta(days=20)).date()],
         "value": [1.1]}), "ecb")
    assert validate.staleness_report(con) == 1
    con2 = _store()
    store.upsert_fx(con2, "EURUSD", pd.DataFrame(
        {"date": [(pd.Timestamp.today() - pd.Timedelta(days=2)).date()],
         "value": [1.1]}), "ecb")
    assert validate.staleness_report(con2) == 0


def test_a_due_release_is_news_not_a_problem(capsys, monkeypatch):
    """A series whose next period is due prints a line and counts nothing: the
    next question about it fetches it (ops/freshness.py)."""
    from bedrok.core import releases
    monkeypatch.setattr(releases, "load", lambda: (releases.parse_row(
        {"series_id": "tst_cpi", "prefix": "", "frequency": "monthly", "lag_min": "5",
         "lag_max": "200", "weekday": "", "time": "08:30", "tz": "America/New_York",
         "note": "test"}),))
    con = _store()
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "tst_cpi", "source": "bls", "source_code": "x", "name": "Test CPI",
         "frequency": "monthly", "staleness_days": 400}]))
    three_back = (pd.Timestamp.today().normalize().replace(day=1) - pd.DateOffset(months=3)).date()
    store.upsert_macro_series(con, "tst_cpi", pd.DataFrame({"date": [three_back], "value": [1.0]}))
    assert validate.staleness_report(con) == 0
    out = capsys.readouterr().out
    assert "due" in out and "tst_cpi" in out and "not in the store yet" in out
