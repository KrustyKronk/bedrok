"""One staleness rule, shared by validate, doctor and the MCP server."""
from datetime import date

import duckdb
import pandas as pd

from bedrok.core import store
from bedrok.ops import freshness


def _store(rows, data):
    con = duckdb.connect(":memory:")
    store.init_db(con)
    store.upsert_macro_catalog(con, pd.DataFrame(rows))
    for sid, d in data.items():
        store.upsert_macro_series(con, sid, pd.DataFrame({"date": [pd.Timestamp(d).date()], "value": [1.0]}))
    return con


def test_fresh_stale_empty_and_session():
    con = _store([
        {"series_id": "a", "source": "x", "source_code": "x", "name": "a", "frequency": "monthly"},
        {"series_id": "b", "source": "x", "source_code": "x", "name": "b", "frequency": "monthly"},
        {"series_id": "c", "source": "x", "source_code": "x", "name": "c", "frequency": "daily"},
        {"series_id": "d", "source": "fred", "source_code": "x", "name": "d", "frequency": "daily",
         "cache": "session"},
    ], {"a": "2026-08-01", "b": "2026-01-01"})
    today = date(2026, 10, 10)
    assert freshness.assess(con, "a", today)["status"] == "fresh"       # 70d < 90d
    b = freshness.assess(con, "b", today)
    assert b["status"] == "stale" and b["age_days"] == 282 and b["allowance_days"] == 90
    assert freshness.assess(con, "c", today)["status"] == "empty"
    assert freshness.assess(con, "d", today)["status"] == "session"
    assert freshness.assess(con, "nope", today) is None


def test_catalog_override_and_bulk_family_allowances():
    assert freshness.allowance("quarterly", "330") == 330
    assert freshness.allowance("monthly", None, "wb_gold") == 90
    assert freshness.allowance("weekly", None, "cot_gold_net_noncomm") == 30
    assert freshness.allowance(None, None) == 90          # unknown frequency → monthly
    assert freshness.allowance("annual", None) == 450


def test_a_forecast_never_makes_a_series_look_fresh():
    """Session 11: IMF WEO rows run to 2031; their age counts from the last
    observation that is not in the future, and how far ahead they run is said."""
    con = duckdb.connect(":memory:")
    store.init_db(con)
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "weo", "source": "imf", "source_code": "x", "name": "weo",
         "frequency": "annual"}]))
    store.upsert_macro_series(con, "weo", pd.DataFrame({
        "date": [date(2024, 1, 1), date(2025, 1, 1), date(2031, 1, 1)], "value": [1.0, 2.0, 3.0]}))
    f = freshness.assess(con, "weo", date(2026, 10, 10))
    assert f["last_date"] == date(2025, 1, 1) and f["future_to"] == date(2031, 1, 1)
    assert f["age_days"] == (date(2026, 10, 10) - date(2025, 1, 1)).days
    assert f["status"] == "stale"            # 647 days > 450: no forecast hides it
