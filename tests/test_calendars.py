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
    cat = set(pd.read_csv(pipeline.CATALOG_CSV, dtype=str)["series_id"])
    missing = [r["series_id"] for r in rows if r["series_id"] not in cat]
    assert not missing, f"calendar rows for series not in the catalog: {missing}"


# ---- session H: four more publishers --------------------------------------------

def _series(sid, source, frequency, held, staleness=150):
    return _con([{"series_id": sid, "source": source, "source_code": "x", "name": sid,
                  "frequency": frequency, "staleness_days": staleness}], {sid: held})


FED_NOW = datetime(2026, 11, 23, 15, tzinfo=timezone.utc)   # the weeks to 11 and 18 Nov are out


def _next(con, feed, df, sid, now=NOW):
    store.replace_release_calendar(con, feed, calendars._frame(feed, df, now))
    return freshness.assess(con, sid, now=now)["next_release"]


# A fragment of Statistics Canada's key-indicator schedule, 2026-10-16.
STATCAN = [
    {"date": "2026-10-09 00:00:01", "title": "Labour Force Survey", "description": "September 2026"},
    {"date": "2026-10-19 00:00:01", "title": "Consumer Price Index", "description": "September 2026"},
    {"date": "2026-11-30 00:00:01", "title": "Gross domestic product, income and expenditure",
     "description": "third quarter 2026"},
]


def test_statistics_canada_dates_the_cpi_at_0830_toronto():
    df = calendars.parse_statcan(STATCAN)
    assert len(df) == 3 and df["period"].iloc[2] == date(2026, 7, 1)
    nr = _next(_series("ca_cpi", "statcan", "monthly", date(2026, 8, 1)),
               "statcan_json", df, "ca_cpi")
    assert nr["period"] == "September 2026" and nr["due_at"] == "2026-10-19T08:30:00-04:00"
    assert nr["publisher"] == "Statistics Canada" and nr["approximate"] is False


# A fragment of INE's iCal file, 2026-10-16: a final, a flash in Spanish, and a
# folded line.
INE = "\r\n".join([
    "BEGIN:VCALENDAR", "BEGIN:VEVENT", "DTSTART;TZID=Europe/Madrid:20261030T090000",
    "SUMMARY:Consumer Price Index. Avance. Octubre 2026", "END:VEVENT",
    "BEGIN:VEVENT", "DTSTART;TZID=Europe/Madrid:20261113T090000",
    "SUMMARY:Consumer Price Index. October 2026", "END:VEVENT",
    "BEGIN:VEVENT", "DTSTART;TZID=Europe/Madrid:20261027T090000",
    "SUMMARY:Economically Active Population Survey. Quar", " ter 3/2026", "END:VEVENT",
    "END:VCALENDAR"])


def test_ine_dates_the_final_cpi_and_ignores_the_flash():
    df = calendars.parse_ine(INE)
    assert sorted(df["release_name"]) == ["Consumer Price Index",
                                          "Economically Active Population Survey"]
    nr = _next(_series("es_cpi_ine", "ine", "monthly", date(2026, 9, 1), 90),
               "ine_ics", df, "es_cpi_ine")
    assert nr["period"] == "October 2026" and nr["due_at"] == "2026-11-13T09:00:00+01:00"
    nr = _next(_series("es_unemployment_epa", "ine", "quarterly", date(2026, 4, 1), 220),
               "ine_ics", df, "es_unemployment_epa")
    assert nr["period"] == "Q3 2026" and nr["due_at"] == "2026-10-27T09:00:00+01:00"


# A fragment of the ECB's statistical calendar page, 2026-10-16.
ECB = """<dl>
<dt> 27/10/2026 10:00 </dt> <dd> Euro area Bank Lending Survey (Dataset: BLS)<br>
 Reference period: Q3 2026<br> Includes press release.<br> </dd>
<dt> 27/10/2026 10:00 </dt> <dd> National balance sheet of euro area monetary financial
 institutions, excluding the Eurosystem (Dataset: BSI)<br> Reference period: Sep-2026<br> </dd>
<dt> 27/10/2026 10:00 </dt> <dd> Monetary developments in the euro area (Dataset: BSI)<br>
 Reference period: Sep-2026<br> Includes press release.<br> </dd>
<dt> 02/11/2026 10:00 </dt> <dd> Euro area bank interest rate statistics (Dataset: MIR)<br>
 Reference period: Sep-2026<br> </dd>
<dt> 04/11/2026 15:00 </dt> <dd> Euro area seasonally adjusted HICP flash estimate
 (Dataset: HICP)<br> Reference period: Oct-2026<br> </dd>
</dl>"""


def test_the_ecb_dates_money_rates_and_its_lending_survey():
    df = calendars.parse_ecb(ECB)
    assert sorted(df["release_name"]) == ["BLS", "BSI", "MIR"]   # its HICP is not Eurostat's
    assert df.loc[df["release_name"] == "BLS", "period"].isna().all()
    nr = _next(_series("ea_m3_growth", "ecb", "monthly", date(2026, 8, 1), 100),
               "ecb_statscal", df, "ea_m3_growth")
    assert nr["period"] == "September 2026" and nr["due_at"] == "2026-10-27T10:00:00+01:00"
    assert nr["publisher"] == "ECB"
    nr = _next(_series("ea_cob_nfc", "ecb", "monthly", date(2026, 8, 1), 120),
               "ecb_statscal", df, "ea_cob_nfc")
    assert nr["due_at"] == "2026-11-02T10:00:00+01:00"
    # The survey is dated by its own quarter: the October round is Q4 here,
    # whatever reference period the ECB's page gives it.
    nr = _next(_series("ea_bls_cs_enterprises", "ecb", "quarterly", date(2026, 7, 1)),
               "ecb_statscal", df, "ea_bls_cs_enterprises")
    assert nr["period"] == "Q4 2026" and nr["due_at"] == "2026-10-27T10:00:00+01:00"
    assert nr["source"] == "calendar"


def _fed_row(clock, name, days):
    return (f'<div class="col-xs-2"> <p>{clock}</p> </div> <div class="col-xs-7"> '
            f'<p>{name}</p> <p></p> </div> <div class="col-xs-3"> <p>{days}</p> </div>')


# Rows of the Fed Board's November 2026 calendar page.
FED_NOV = "\n".join([
    _fed_row("2:00 p.m.", "Senior Loan Officer Opinion Survey on Bank Lending Practices (SLOOS)", "2"),
    _fed_row("4:30 p.m.", "H.4.1 - Factors Affecting Reserve Balances", "5, 12, 19, 27"),
    _fed_row("4:15 p.m.", "H.8 - Assets and Liabilities of Commercial Banks in the U.S.",
             "6, 13, 20, 27"),
    _fed_row("", "G.20 - Finance Companies", "30"),
    _fed_row("", "Holiday - Thanksgiving Day", "26"),
])


def test_the_fed_board_pages_date_each_release_on_each_listed_day():
    df = calendars.parse_fedboard(FED_NOV, 2026, 11)
    assert sorted(set(df["release_name"])) == ["H.4.1", "H.8", "SLOOS"]   # no time, no row
    assert len(df) == 9 and df["period"].isna().all()
    # Thanksgiving week: the H.4.1 for the week to Wednesday 25 Nov comes on Friday.
    nr = _next(_series("us_fed_balance_sheet", "fedboard", "weekly", date(2026, 11, 18), 21),
               "fedboard_pages", df, "us_fed_balance_sheet", FED_NOW)
    assert nr["due_at"] == "2026-11-27T16:30:00-05:00"
    nr = _next(_series("us_sloos_cni_tightening", "fedboard", "quarterly", date(2026, 7, 1)),
               "fedboard_pages", df, "us_sloos_cni_tightening")
    assert nr["period"] == "Q4 2026" and nr["due_at"] == "2026-11-02T14:00:00-05:00"
    assert nr["source"] == "calendar"                 # published inside its own quarter


def test_a_release_nine_days_after_its_week_is_not_mistaken_for_the_last_one():
    """Session H: the first H.8 after the week to 18 Nov ends is the 20 Nov one,
    which carries the week to 11 Nov. The match starts from the rule's own
    earliest date, so the week to 18 Nov gets 27 Nov."""
    df = calendars.parse_fedboard(FED_NOV, 2026, 11)
    nr = _next(_series("us_bank_credit", "fedboard", "weekly", date(2026, 11, 11), 21),
               "fedboard_pages", df, "us_bank_credit", FED_NOW)
    assert nr["due_at"] == "2026-11-27T16:15:00-05:00"


def test_the_fed_page_after_next_is_used_only_when_it_is_up(monkeypatch):
    asked = []

    class R:
        def __init__(self, url):
            self.status_code = 404 if "2027-january" in url else 200
            self.text = FED_NOV

        def raise_for_status(self):
            if self.status_code >= 400:
                raise OSError(self.status_code)

    monkeypatch.setattr(calendars.requests, "get",
                        lambda url, **k: asked.append(url) or R(url))
    df = calendars.fetch_fedboard(date(2026, 11, 16))
    assert [u.rsplit("/", 1)[1] for u in asked] == [
        "2026-november.htm", "2026-december.htm", "2027-january.htm"]
    assert len(df) == 18                               # November's rows read twice, as December's
