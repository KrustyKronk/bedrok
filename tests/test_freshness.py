"""One freshness rule, shared by validate and the MCP server: age, and where a
release rule exists, whether the next period is due."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

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


def _cpi_store(staleness="90", source="bls", sid="us_cpi"):
    return _store([{"series_id": sid, "source": source, "source_code": "x", "name": "US CPI",
                    "frequency": "monthly", "staleness_days": staleness}],
                  {sid: "2026-08-01"})


def _et(*a):
    return datetime(*a, tzinfo=ZoneInfo("America/New_York"))


def test_the_morning_the_cpi_came_out_is_due_not_fresh():
    """2026-10-15: August stored, 75 days old against 90. Age alone said fresh."""
    con = _cpi_store()
    before = freshness.assess(con, "us_cpi", now=_et(2026, 10, 11, 12))
    assert before["status"] == "fresh" and before["age_days"] == 71
    assert before["next_release"]["message"].startswith("Next: September 2026, usually out")
    f = freshness.assess(con, "us_cpi", now=_et(2026, 10, 15, 9))
    assert f["status"] == "due" and f["stale"] is False and f["age_days"] == 75
    assert f["next_release"]["period"] == "September 2026"
    assert "August 2026 is the latest held" in f["next_release"]["message"]
    assert freshness.assess(con, "us_cpi", now=_et(2026, 10, 22, 12))["status"] == "late"


def test_staleness_still_overrides_a_late_release():
    con = _cpi_store(staleness="80")
    f = freshness.assess(con, "us_cpi", now=_et(2026, 10, 22, 12))
    assert f["status"] == "stale" and f["next_release"]["period"] == "September 2026"


def test_a_series_without_a_rule_is_judged_by_age_alone():
    con = _cpi_store(sid="no_rule_here", source="x")
    f = freshness.assess(con, "no_rule_here", now=_et(2026, 10, 22, 12))
    assert f["status"] == "fresh" and f["next_release"] is None


def test_a_session_series_says_when_its_next_release_is():
    con = _store([{"series_id": "us_initial_claims", "source": "fred", "source_code": "ICSA",
                   "name": "claims", "frequency": "weekly", "cache": "session"}], {})
    f = freshness.assess(con, "us_initial_claims", now=_et(2026, 10, 15, 9))
    assert f["status"] == "session"
    assert f["next_release"]["due_at"] == "2026-10-22T08:30:00-04:00"


def test_a_derived_series_is_due_when_its_input_is():
    """us_cpi_3m_annualized has no rule of its own; it moves with us_cpi."""
    con = _cpi_store(sid="us_cpi_3m_annualized", source="derived")
    f = freshness.assess(con, "us_cpi_3m_annualized", now=_et(2026, 10, 15, 9))
    assert f["status"] == "due" and f["next_release"]["period"] == "September 2026"


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
