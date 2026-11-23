"""The publishers' own release dates (ingest/calendars.py, the release_calendar
table) and how freshness uses them. Inline payloads, no network."""
import csv
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import duckdb
import pytest
import pandas as pd

from bedrok.core import releases, store
from bedrok.ingest import calendars, pipeline, world
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
    keys = [r["series_id"] or r["prefix"] for r in rows]
    assert len(keys) == len(set(keys))
    assert all(bool(r["series_id"]) != bool(r["prefix"]) for r in rows)
    families = tuple(pipeline.BULK_STAGE_FOR_PREFIX) + tuple(pipeline.BULK_PREFIXES) + world.PREFIXES
    for r in rows:
        assert r["feed"] in releases.FEEDS
        if r["prefix"]:
            assert r["prefix"] in families, f"unknown family {r['prefix']}"
            assert releases.rule_for(r["prefix"]), f"{r['prefix']}: a calendar needs a rule to bound it"
            assert r["release_name"], r["prefix"]
            continue
        if r["feed"] in releases.MEETING_FEEDS:
            # A policy rate is bounded by its bank's decisions, not by a rule;
            # a rule as well would judge it twice.
            assert not releases.rule_for(r["series_id"]), r["series_id"]
            assert r["release_name"] == releases.FEEDS[r["feed"]]["decision"], r["series_id"]
            continue
        assert releases.rule_for(r["series_id"]), f"{r['series_id']}: a calendar needs a rule to bound it"
        meta = releases.FEEDS[r["feed"]]
        assert meta["fetched"] == (r["feed"] in calendars.FETCHERS), r["feed"]
        if meta["fetched"]:
            assert r["release_name"], f"{r['series_id']}: a fetched feed needs a release name"
    cat = set(pd.read_csv(pipeline.CATALOG_CSV, dtype=str)["series_id"])
    cat |= set(pipeline.static_catalog_rows()["series_id"])
    missing = [r["series_id"] for r in rows if r["series_id"] and r["series_id"] not in cat]
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


# ---- session K1: the National Bank of Kazakhstan's "Next:" dates ------------------

KZ_NOW = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)


def _kz(sid, held=date(2026, 9, 1)):
    return _con([{"series_id": sid, "source": "nbk", "source_code": "records:x/y#z", "name": sid,
                  "frequency": "monthly", "staleness_days": 100}], {sid: held})


def test_the_national_bank_date_is_kept_at_the_rule_time_in_almaty():
    entry = calendars.parse_nbk_next(date(2026, 11, 16), releases.rule_for("kz_m3")["at"])
    assert entry == {"period": None, "due_at": datetime(2026, 11, 16, 5, 0)}   # 10:00 at UTC+5
    assert calendars.parse_nbk_next(None, releases.rule_for("kz_m3")["at"]) is None
    con = _kz("kz_m3")
    calendars.record_publisher(con, "kz_m3", entry, KZ_NOW, feed="nbk_next_release")
    nr = freshness.assess(con, "kz_m3", now=KZ_NOW)["next_release"]
    assert nr["due_at"] == "2026-11-16T10:00:00+05:00"
    assert nr["publisher"] == "National Bank of Kazakhstan" and nr["approximate"] is False


def test_a_reserves_revision_date_is_not_taken_for_the_next_month():
    """The reserves page's "Next: 30.10.2026" is September's final figure, a
    month already held; October's first figure is due about 9 November."""
    con = _kz("kz_reserves_gross")
    calendars.record_publisher(con, "kz_reserves_gross", calendars.parse_nbk_next(
        date(2026, 10, 30), releases.rule_for("kz_reserves_gross")["at"]), KZ_NOW,
        feed="nbk_next_release")
    nr = freshness.assess(con, "kz_reserves_gross", now=KZ_NOW)["next_release"]
    assert nr["approximate"] is True and nr["period"] == "October 2026"


def test_every_national_bank_calendar_series_waits_three_days_into_its_window():
    """A period-less date is believed from three days before the rule's
    earliest day, so a rule starting sooner would let a revision date through."""
    with open(releases.CALENDAR_CSV, newline="", encoding="utf-8") as f:
        sids = [r["series_id"] for r in csv.DictReader(f) if r["feed"] == "nbk_next_release"]
    assert len(sids) == 7
    for sid in sids:
        assert releases.rule_for(sid)["lag_min"] >= 3, sid


def test_the_pipeline_asks_the_page_only_for_a_series_with_a_calendar(monkeypatch):
    from bedrok.ingest.adapters import nbk
    asked = []
    monkeypatch.setattr(nbk, "next_release", lambda code: asked.append(code) or date(2026, 11, 16))
    con = _kz("kz_m3")
    pipeline._record_next_release(con, "kz_m3", {"source": "nbk", "source_code": "records:a/b#m_3"})
    pipeline._record_next_release(con, "kz_reer", {"source": "nbk", "source_code": "api:299?c=x"})
    assert asked == ["records:a/b#m_3"]
    got = con.execute("SELECT feed, release_name, due_at FROM release_calendar").fetchall()
    assert got == [("nbk_next_release", "kz_m3", datetime(2026, 11, 16, 5, 0))]


# ---- session K2: the Bureau of National Statistics of Kazakhstan's calendar ---------

def _bns_event(day, title, kind="Publication"):
    return (f'<div class="calendar-event " id="bx_1"><div class="calendar-event-day">\n'
            f'    {day}                    </div>\n\n        {title}          \n'
            f'<div class="calendar-event-type">  {kind}  </div></div>')


# Cut from the October and November 2026 pages, 2026-10-29: a past month's
# titles carry their period, a coming month's do not; one wages title is spelt
# "emloyees"; industry is listed twice on one day.
BNS_OCT = "".join([
    _bns_event("01.10.2026", "Inflation in the Republic of Kazakhstan (September 2026 )"),
    _bns_event("09.10.2026", "Price indices and prices in the housing market in the Republic of "
                             "Kazakhstan (September 2026)"),
    _bns_event("16.10.2026", "Main indicators of industry performance (January-September 2026)"),
    _bns_event("16.10.2026", "Main indicators of industry performance in the Republic of Kazakhstan "
                             "(January-September 2026)"),
    _bns_event("30.10.2026", "Fixed assets of the Republic of Kazakhstan", "Spreadsheet"),
])
BNS_NOV = "".join([
    _bns_event("02.11.2026", "Inflation in the Republic of Kazakhstan"),
    _bns_event("10.11.2026", "Number and wages of emloyees in the Republic of Kazakhstan"),
    _bns_event("16.11.2026", "GDP by method of production"),
])
BNS_NOW = datetime(2026, 10, 29, 9, tzinfo=timezone.utc)


def test_the_bureau_calendar_keeps_its_releases_and_their_periods():
    oct_ = calendars.parse_bns(BNS_OCT)
    # housing is published here but reaches Taldau weeks later: on its rule instead
    assert oct_["release_name"].tolist() == ["Inflation", "Industry"]
    assert oct_["period"].iloc[0] == date(2026, 9, 1)
    assert pd.isna(oct_["period"].iloc[1])                 # "January-September" is not a month
    assert oct_["due_at"].iloc[0] == datetime(2026, 10, 1, 5, 0)   # 10:00 at UTC+5
    nov = calendars.parse_bns(BNS_NOV)
    assert nov["release_name"].tolist() == ["Inflation", "Wages"]   # GDP has no calendar row


def test_the_next_cpi_date_is_the_bureaus():
    df = pd.concat([calendars.parse_bns(BNS_OCT), calendars.parse_bns(BNS_NOV)], ignore_index=True)
    nr = _next(_series("kz_cpi_yoy", "bns", "monthly", date(2026, 9, 1), 90),
               "bns_calendar", df, "kz_cpi_yoy", BNS_NOW)
    assert nr["due_at"] == "2026-11-02T10:00:00+05:00" and nr["approximate"] is False
    assert nr["publisher"] == "Bureau of National Statistics of Kazakhstan"


def test_the_bureau_calendar_asks_for_this_month_and_the_next(monkeypatch):
    asked = []

    class R:
        status_code = 200

        def __init__(self, url):
            self.text = BNS_NOV if "11.2026" in url else BNS_OCT

        def raise_for_status(self):
            pass
    monkeypatch.setattr(calendars.requests, "get", lambda url, **k: asked.append(url) or R(url))
    df = calendars.fetch_bns(date(2026, 10, 29))
    assert [u.rsplit("=", 1)[1] for u in asked] == ["10.2026", "11.2026"]
    assert len(df) == 4


# ---- 2026-11-23: Eurostat, the BIS, families and editions -------------------------

# A fragment of https://ec.europa.eu/eurostat/o/calendars/eventsIcal, 2026-11-23:
# all-day events, LF line endings, an escaped comma, articles beside releases.
EUROSTAT = (
    "BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART;VALUE=DATE:20261201\n"
    "SUMMARY:Flash estimate inflation euro area\n"
    "X-CATEGORY:Data release\\,Euro indicators release\\,Statistics Explained\nEND:VEVENT\n"
    "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20261202\nSUMMARY:Unemployment\n"
    "X-CATEGORY:Data release\\,Euro indicators release\nEND:VEVENT\n"
    "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20261130\nSUMMARY:Unemployment\n"
    "X-CATEGORY:Statistics Explained\nEND:VEVENT\n"
    "BEGIN:VEVENT\nDTSTART;VALUE=DATE:20261203\nSUMMARY:Industrial producer prices\\, domes\n"
    " tic market\nX-CATEGORY:Data release\\,Euro indicators release\nEND:VEVENT\n"
    "END:VCALENDAR\n")


def test_eurostat_keeps_its_euro_indicator_releases_at_eleven_luxembourg():
    df = calendars.parse_eurostat(EUROSTAT)
    assert list(df["release_name"]) == ["Flash estimate inflation euro area", "Unemployment"]
    assert list(df["due_at"]) == [datetime(2026, 12, 1, 10), datetime(2026, 12, 2, 10)]
    assert df["period"].isna().all()                 # the file names no period


def test_the_flash_date_is_eurostats_and_its_time_is_usual():
    con = _series("ea_hicp_yoy", "eurostat", "monthly", date(2026, 10, 1), 90)
    nr = _next(con, "eurostat_ics", calendars.parse_eurostat(EUROSTAT), "ea_hicp_yoy",
               now=datetime(2026, 11, 23, 12, tzinfo=timezone.utc))
    assert nr["period"] == "November 2026" and nr["due_at"] == "2026-12-01T11:00:00+01:00"
    assert nr["approximate"] is True and nr["date_exact"] is True
    assert "1 Dec 2026, usually 11:00 CET (Eurostat's release calendar)" in nr["message"]


# A fragment of https://data.bis.org/feed.xml, 2026-11-23.
BIS = """<rss><channel>
<item><title><![CDATA[Consumer prices, 2026-10 / Release]]></title><category><![CDATA[CPI]]></category>
<pubDate>Thu, 26 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title><![CDATA[Bilateral exchange rates, 2026-11 / Daily and monthly data]]></title>
<category><![CDATA[XRU]]></category><pubDate>Thu, 03 Dec 2026 10:00:00 GMT</pubDate></item>
<item><title><![CDATA[Bilateral exchange rates, 2026-11 / Daily and monthly data]]></title>
<category><![CDATA[XRU]]></category><pubDate>Thu, 10 Dec 2026 10:00:00 GMT</pubDate></item>
<item><title><![CDATA[Effective exchange rates, 2026-11-30 / Nominal daily data]]></title>
<category><![CDATA[EER]]></category><pubDate>Thu, 03 Dec 2026 10:00:00 GMT</pubDate></item>
<item><title><![CDATA[Effective exchange rates, 2026-11 / Nominal / real monthly data]]></title>
<category><![CDATA[EER]]></category><pubDate>Thu, 17 Dec 2026 10:00:00 GMT</pubDate></item>
<item><title><![CDATA[Residential property prices, 2026-10 / Detailed residential release]]></title>
<category><![CDATA[RPP]]></category><pubDate>Wed, 23 Dec 2026 10:00:00 GMT</pubDate></item>
<item><title><![CDATA[Residential property prices, 2026-Q3 / Selected residential, partial update 1]]></title>
<category><![CDATA[RPP]]></category><pubDate>Wed, 23 Dec 2026 10:00:00 GMT</pubDate></item>
<item><title><![CDATA[Credit to the non-financial sector, 2026-Q2 / Release]]></title>
<category><![CDATA[TOTAL_CREDIT]]></category><pubDate>Mon, 07 Dec 2026 10:00:00 GMT</pubDate></item>
<item><title><![CDATA[Central bank total assets, 2026-09 / Release]]></title>
<category><![CDATA[CBTA]]></category><pubDate>Thu, 26 Nov 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_the_bis_feed_names_each_release_and_its_period():
    df = calendars.parse_bis(BIS)
    got = {(r.release_name, r.due_at.date(), r.period) for r in df.itertuples()}
    assert got == {("CPI", date(2026, 11, 26), date(2026, 10, 1)),
                   ("XRU", date(2026, 12, 3), date(2026, 11, 1)),
                   ("XRU", date(2026, 12, 10), date(2026, 11, 1)),
                   ("EER monthly", date(2026, 12, 17), date(2026, 11, 1)),
                   ("RPP selected", date(2026, 12, 23), date(2026, 7, 1)),
                   ("TOTAL_CREDIT", date(2026, 12, 7), date(2026, 4, 1))}


def _family(members: dict, frequency="monthly", staleness=150):
    return _con([{"series_id": s, "source": "bis", "source_code": "x", "name": s,
                  "frequency": frequency, "staleness_days": staleness} for s in members], members)


def test_a_family_takes_the_first_bis_release_of_its_period():
    con = _family({"bis_usd_rate_br": date(2026, 10, 1)})
    now = datetime(2026, 12, 3, 12, tzinfo=timezone.utc)
    nr = _next(con, "bis_rss", calendars.parse_bis(BIS), "bis_usd_rate_br", now=now)
    assert nr["period"] == "November 2026" and nr["due_at"].startswith("2026-12-03")
    assert nr["publisher"] == "BIS" and "releasing no later than it" in nr["message"]
    assert freshness.assess(con, "bis_usd_rate_br", now=now)["status"] == "due"


def test_a_member_the_publisher_left_out_is_behind_not_late():
    """The BIS collects small economies' dollar rates months late: Ghana's
    August while Brazil's October is out is the BIS's gap, not Bedrok's."""
    con = _family({"bis_usd_rate_br": date(2026, 10, 1), "bis_usd_rate_gh": date(2026, 8, 1)})
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    f = freshness.assess(con, "bis_usd_rate_gh", now=now)
    assert f["status"] == "fresh" and f["next_release"]["behind"] is True
    assert "out for other series in this family" in f["next_release"]["message"]
    assert "August 2026 is the latest held" in f["next_release"]["message"]
    # Past its allowance it is stale all the same: behind is not a pardon.
    old = _family({"bis_usd_rate_br": date(2026, 10, 1), "bis_usd_rate_gh": date(2026, 5, 1)})
    assert freshness.assess(old, "bis_usd_rate_gh", now=now)["status"] == "stale"


def test_a_series_with_its_own_rule_is_never_behind_a_family():
    con = _con([{"series_id": s, "source": "fedboard", "source_code": "x", "name": s,
                 "frequency": "monthly", "staleness_days": 90} for s in ("us_ci_loans", "us_cix")],
               {"us_ci_loans": date(2026, 9, 1), "us_cix": date(2026, 10, 1)})
    assert releases.family_of("us_ci_loans") is None
    f = freshness.assess(con, "us_ci_loans", now=datetime(2026, 11, 20, 23, tzinfo=timezone.utc))
    assert f["status"] == "late"


def test_a_family_calendar_entry_answers_every_member():
    assert releases.calendar_key("bis_cpi_yoy_br") == ("bis_rss", "CPI")
    assert releases.calendar_key("bis_credit_gap_cn") == ("bis_rss", "CREDIT_GAPS")
    assert releases.family_of("bis_cpi_yoy_br") == "bis_cpi_yoy_"
    assert releases.calendar_key("us_ci_loans") == ("fedboard_pages", "H.8")


def test_the_weo_says_its_next_edition_and_is_judged_by_age():
    con = _con([{"series_id": "weo_gdp_growth_br", "source": "imf", "source_code": "x",
                 "name": "x", "frequency": "annual", "staleness_days": 450}],
               {"weo_gdp_growth_br": date(2026, 1, 1)})
    store.upsert_macro_series(con, "weo_gdp_growth_br",
                              pd.DataFrame({"date": [date(2027, 1, 1), date(2031, 1, 1)],
                                            "value": [2.0, 2.0]}))
    f = freshness.assess(con, "weo_gdp_growth_br", now=datetime(2026, 11, 23, tzinfo=timezone.utc))
    nr = f["next_release"]
    assert f["status"] == "fresh" and nr["informational"] is True
    assert nr["period"] == "the April 2027 World Economic Outlook"
    assert "between 8 and 24 Apr 2027" in nr["message"]
    # Inside the window it is still only said: an edition never makes a series due.
    f = freshness.assess(con, "weo_gdp_growth_br", now=datetime(2027, 4, 20, tzinfo=timezone.utc))
    assert f["status"] == "fresh" and f["next_release"]["period"].startswith("the April 2027")
    # Once the window has closed, the next one is October's.
    f = freshness.assess(con, "weo_gdp_growth_br", now=datetime(2027, 5, 1, tzinfo=timezone.utc))
    assert f["next_release"]["period"] == "the October 2027 World Economic Outlook"


# ---- the National Bank of Kazakhstan's decisions (2026-11-23) ---------------------

# Fragments of nationalbank.kz/en/news/grafik-prinyatiya-resheniy-po-bazovoy-stavke
# and its 2026 page (rubrics/2365), 2026-11-23.
NBK_SECTION = (
    '<li><a href="/en/news/grafik-prinyatiya-resheniy-po-bazovoy-stavke/rubrics/2365" '
    'class="x"><span>2026</span></a></li>'
    '<li><a href="/en/news/grafik-prinyatiya-resheniy-po-bazovoy-stavke/rubrics/2237">2025</a></li>'
    '<li><a href="/en/news/grafik-prinyatiya-resheniy-po-bazovoy-stavke/rubrics/2365"></a></li>'
    '<li><a href="/en/news/statistical-bulletin/rubrics/2329">NBK Statistical Bulletin</a></li>')
NBK_2026 = (
    '<table><tbody><tr><td><p>Date</p></td><td>Base rate</td><td>Corridor</td><td></td></tr>'
    '<tr style="height: 100px;"><td style="text-align: center;"><p><span>27.10.2026</span></p></td>'
    '<td>15.75</td><td>14.75 - 16.75</td><td><a href="x">Press-release</a></td></tr>'
    '<tr style="height: 100px;"><td style="text-align: center;"><span style="font-size: 18px;">'
    '04.12.2026*</span></td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>'
    '</tbody></table><p><em>*Base rate announcement date&nbsp;</em></p>')


def test_the_national_banks_year_page_is_found_by_its_label():
    years = calendars.parse_nbk_rubrics(NBK_SECTION)
    assert years == {
        2026: "https://nationalbank.kz/en/news/grafik-prinyatiya-resheniy-po-bazovoy-stavke/rubrics/2365",
        2025: "https://nationalbank.kz/en/news/grafik-prinyatiya-resheniy-po-bazovoy-stavke/rubrics/2237"}


def test_the_national_bank_schedule_lists_each_decision_day():
    df = calendars.parse_nbk_schedule(NBK_2026)
    assert list(df["due_at"]) == [datetime(2026, 10, 27, 7), datetime(2026, 12, 4, 7)]
    assert set(df["release_name"]) == {"National Bank of Kazakhstan base rate decision"}


def test_a_missing_year_page_fails_rather_than_guessing(monkeypatch):
    class R:
        text = NBK_SECTION
        def raise_for_status(self):
            pass
    monkeypatch.setattr(calendars.requests, "get", lambda *a, **k: R())
    with pytest.raises(ValueError, match="no 2027 page"):
        calendars.fetch_nbk_schedule(date(2027, 1, 5))


def _kz_rate(held):
    con = _con([{"series_id": "kz_base_rate", "source": "nbk", "source_code": "base-rate",
                 "name": "x", "frequency": "event", "staleness_days": 150}],
               {"kz_base_rate": held})
    store.replace_release_calendar(con, "nbk_base_rate", calendars._frame(
        "nbk_base_rate", calendars.parse_nbk_schedule(NBK_2026), KZ_NOW))
    return con


def test_the_base_rate_is_due_on_decision_day_and_a_hold_is_a_row():
    """Every decision, holds included, has a row dated on its day, so a hold
    shows like a change, and a fetch that morning before the announcement
    does not count it as shown."""
    con = _kz_rate(date(2026, 10, 27))
    before = datetime(2026, 12, 1, 6, tzinfo=timezone.utc)
    f = freshness.assess(con, "kz_base_rate", now=before)
    assert f["status"] == "fresh" and "4 Dec 2026" in f["next_release"]["message"]
    assert "gives the day, not the time" in f["next_release"]["message"]
    day = datetime(2026, 12, 4, 9, tzinfo=timezone.utc)
    store.record_check(con, "kz_base_rate", datetime(2026, 12, 4, 4))   # 09:00 Almaty, before it
    assert freshness.assess(con, "kz_base_rate", now=day)["status"] == "due"
    store.upsert_macro_series(con, "kz_base_rate",
                              pd.DataFrame({"date": [date(2026, 12, 4)], "value": [15.75]}))
    assert freshness.assess(con, "kz_base_rate", now=day)["status"] == "fresh"
