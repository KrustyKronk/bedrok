"""The publishers' own release dates (ingest/calendars.py, the release_calendar
table) and how freshness uses them. Inline payloads, no network."""
import csv
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from bedrok.core import releases, store
from bedrok.ingest import calendars, pipeline
from bedrok.ingest.adapters import ons
from bedrok.ops import freshness

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 15, 13, tzinfo=timezone.utc)

# A fragment of https://apps.bea.gov/API/signup/release_dates.json, 2026-10-15.
BEA = {
    "Personal Income and Outlays": {"release_dates": [
        "2026-08-29T12:30:00+00:00", "2026-09-30T12:30:00+00:00",
        "2026-10-29T12:30:00+00:00", "2026-11-25T13:30:00+00:00"]},
    "Gross Domestic Product": {"release_dates": [
        "2026-09-30T12:30:00+00:00", "2026-10-29T12:30:00+00:00"]},
    "file_last_updated": "2026-10-01T14:00:00+00:00",
}


def _con(rows=(), data=None):
    con = duckdb.connect(":memory:")
    store.init_db(con)
    if rows:
        store.upsert_macro_catalog(con, pd.DataFrame(list(rows)))
    for sid, d in (data or {}).items():
        store.upsert_macro_series(con, sid, pd.DataFrame({"date": [d], "value": [1.0]}))
    return con


def _pce(con=None):
    con = con or _con([{"series_id": "us_pce", "source": "bea", "source_code": "x",
                        "name": "US PCE", "frequency": "monthly", "staleness_days": 105}],
                      {"us_pce": date(2026, 8, 1)})
    calendars.store.replace_release_calendar(
        con, "bea_json", calendars._frame("bea_json", calendars.parse_bea(BEA), NOW))
    return con


def test_bea_schedule_parses_to_utc_and_skips_non_releases():
    df = calendars.parse_bea(BEA)
    assert set(df["release_name"]) == {"Personal Income and Outlays", "Gross Domestic Product"}
    assert df["due_at"].iloc[0] == datetime(2026, 8, 29, 12, 30)


def test_the_release_after_the_period_ends_not_the_one_on_its_last_day():
    """BEA publishes August's PCE on 30 September: September's is the 29 October one."""
    f = freshness.assess(_pce(), "us_pce", now=NOW)
    nr = f["next_release"]
    assert nr["period"] == "September 2026" and nr["source"] == "calendar"
    assert nr["due_at"] == "2026-10-29T08:30:00-04:00" and nr["approximate"] is False
    assert nr["publisher"] == "BEA"
    assert nr["message"] == ("Next: September 2026, scheduled for 29 Oct 2026, 08:30 EDT "
                             "(BEA's release schedule).")
    assert f["status"] == "fresh"


def test_an_exact_date_is_due_at_its_time_and_late_a_day_after():
    con = _pce()
    at = datetime(2026, 10, 29, 8, 31, tzinfo=ET)
    f = freshness.assess(con, "us_pce", now=at)
    assert f["status"] == "due"
    assert f["next_release"]["message"].startswith("September 2026 is scheduled for 29 Oct 2026")
    assert freshness.assess(con, "us_pce", now=datetime(2026, 10, 30, 9, tzinfo=ET))["status"] == "late"


def test_a_date_far_past_the_rule_is_not_believed():
    """A renamed release must not pin a series to a date a year out."""
    con = _con([{"series_id": "us_pce", "source": "bea", "source_code": "x", "name": "PCE",
                 "frequency": "monthly", "staleness_days": 105}], {"us_pce": date(2026, 8, 1)})
    far = pd.DataFrame([{"release_name": "Personal Income and Outlays",
                         "due_at": datetime(2027, 6, 1, 12, 30)}])
    store.replace_release_calendar(con, "bea_json", calendars._frame("bea_json", far, NOW))
    assert freshness.assess(con, "us_pce", now=NOW)["next_release"]["source"] == "rule"


def test_a_store_without_the_table_falls_back_to_the_rule():
    con = _pce()
    con.execute("DROP TABLE release_calendar")
    assert freshness.assess(con, "us_pce", now=NOW)["next_release"]["source"] == "rule"


def test_a_moved_date_replaces_the_old_one():
    con = _pce()
    moved = calendars.parse_bea({"Personal Income and Outlays": {"release_dates": [
        "2026-11-04T13:30:00+00:00"]}})
    store.replace_release_calendar(con, "bea_json", calendars._frame("bea_json", moved, NOW))
    nr = freshness.assess(con, "us_pce", now=NOW)["next_release"]
    assert nr["due_at"] == "2026-11-04T08:30:00-05:00"


def test_the_ons_names_the_next_release_in_the_series_itself():
    entry = calendars.parse_ons_next("21 October 2026", date(2026, 8, 1), "monthly")
    assert entry == {"period": date(2026, 9, 1), "due_at": datetime(2026, 10, 21, 6, 0)}
    assert calendars.parse_ons_next("To be announced", date(2026, 8, 1), "monthly") is None
    con = _con([{"series_id": "uk_cpi_yoy", "source": "ons", "source_code": "x", "name": "UK CPI",
                 "frequency": "monthly", "staleness_days": 110}], {"uk_cpi_yoy": date(2026, 8, 1)})
    calendars.record_publisher(con, "uk_cpi_yoy", entry, NOW)
    calendars.record_publisher(con, "uk_cpi_yoy", entry, NOW)        # again: one row
    assert con.execute("SELECT count(*) FROM release_calendar").fetchone()[0] == 1
    nr = freshness.assess(con, "uk_cpi_yoy", now=NOW)["next_release"]
    assert nr["due_at"] == "2026-10-21T07:00:00+01:00" and nr["publisher"] == "ONS"


def test_the_ons_adapter_remembers_what_it_read(monkeypatch):
    payload = {"description": {"nextRelease": "21 October 2026"},
               "months": [{"year": "2026", "month": "July", "value": "3.8"},
                          {"year": "2026", "month": "August", "value": "3.9"}]}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return payload
    monkeypatch.setattr(ons.requests, "get", lambda *a, **k: R())
    ons.fetch("economy/x/timeseries/d7g7/mm23")
    assert ons.next_release("economy/x/timeseries/d7g7/mm23") == \
        ("21 October 2026", date(2026, 8, 1), "monthly")


def test_a_failed_fetch_keeps_the_stored_dates(monkeypatch):
    con = _pce()
    def boom():
        raise OSError("503")
    monkeypatch.setitem(calendars.FETCHERS, "bea_json", boom)
    name, status, detail = calendars.sync_feed(con, "bea_json")
    assert status == "FAIL" and "kept" in detail
    assert con.execute("SELECT count(*) FROM release_calendar").fetchone()[0] == 6


def test_a_feed_is_refetched_after_a_day():
    con = _pce()
    assert not calendars.needs_fetch(con, "bea_json", NOW)
    assert calendars.needs_fetch(con, "bea_json", datetime(2026, 10, 16, 14, tzinfo=timezone.utc))
    assert calendars.needs_fetch(_con(), "bea_json", NOW)


def test_the_calendar_is_a_refresh_stage():
    assert ("calendar", "Release calendars", calendars.sync_calendars) in pipeline.REFRESH_STAGES


# ---- the seed ------------------------------------------------------------------

def test_every_calendar_series_has_a_rule_and_a_known_feed():
    with open(releases.CALENDAR_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == releases.CALENDAR_COLUMNS
        rows = list(reader)
    assert len(rows) == len({r["series_id"] for r in rows})
    for r in rows:
        assert releases.rule_for(r["series_id"]), f"{r['series_id']}: a calendar needs a rule to bound it"
        assert r["feed"] in releases.FEEDS
        meta = releases.FEEDS[r["feed"]]
        assert meta["fetched"] == (r["feed"] in calendars.FETCHERS), r["feed"]
        if meta["fetched"]:
            assert r["release_name"], f"{r['series_id']}: a fetched feed needs a release name"
