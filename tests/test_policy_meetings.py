"""Central banks' decisions (session I): six meeting schedules read from the
banks' own pages, and the policy rates they make due. Inline fragments of
each page as it was on 2026-11-05, trimmed; no network."""
from datetime import date, datetime, timezone

import anyio
import duckdb
import pandas as pd
import pytest
from mcp import Client

from bedrok.core import config, releases, store
from bedrok.ingest import calendars, pipeline
from bedrok.ops import freshness
from bedrok.surfaces import mcp_server

UTC = timezone.utc

FOMC = """
<div class="panel panel-default"><div class="panel-heading"><h4><a id="42828">2026 FOMC Meetings</a></h4></div>
  <div class="row fomc-meeting" ">
    <div class="fomc-meeting__month col-xs-5 col-sm-3 col-md-2"><strong>September</strong></div>
    <div class="fomc-meeting__date col-xs-4 col-sm-9 col-md-10 col-lg-1">15-16*</div>
  </div>
  <div class="row fomc-meeting" ">
    <div class="fomc-meeting__month col-xs-5 col-sm-3 col-md-2"><strong>October</strong></div>
    <div class="fomc-meeting__date col-xs-4 col-sm-9 col-md-10 col-lg-1">27-28</div>
  </div>
  <div class="row fomc-meeting" ">
    <div class="fomc-meeting__month col-xs-5 col-sm-3 col-md-2"><strong>November</strong></div>
    <div class="fomc-meeting__date col-xs-4 col-sm-9 col-md-10 col-lg-1">13 (unscheduled)</div>
  </div>
  <div class="row fomc-meeting" ">
    <div class="fomc-meeting__month col-xs-5 col-sm-3 col-md-2"><strong>December</strong></div>
    <div class="fomc-meeting__date col-xs-4 col-sm-9 col-md-10 col-lg-1">8-9*</div>
  </div>
<div class="panel-footer">* Meeting associated with a Summary of Economic Projections. </div>
</div>
<div class="panel panel-default"><div class="panel-heading"><h4><a id="45694">2027 FOMC Meetings</a></h4></div>
  <div class="row fomc-meeting" ">
    <div class="fomc-meeting__month col-xs-5 col-sm-3 col-md-2"><strong>Apr/May</strong></div>
    <div class="fomc-meeting__date col-xs-4 col-sm-9 col-md-10 col-lg-1">30-1</div>
  </div>
</div>
"""

ECB = """
<dl>
<dt>
25/11/2026
</dt>
<dd>
Governing Council of the ECB: non-monetary policy meeting (in Frankfurt)<br>
</dd>
<dt>
26/11/2026
</dt>
<dd>
General Council meeting of the ECB in Frankfurt<br>
</dd>
<dt>
16/12/2026
</dt>
<dd>
Governing Council of the ECB: monetary policy meeting in Frankfurt (Day 1)<br>
</dd>
<dt>
17/12/2026
</dt>
<dd>
Governing Council of the ECB: monetary policy meeting in Frankfurt (Day 2), followed by press conference<br>
</dd>
</dl>
"""

BOE = """
<h2>2026 confirmed dates</h2>
<table>
    <tbody>
        <tr>
            <td>Thursday 5 November</td>
            <td><a href="/monetary-policy-summary-and-minutes/2026/november-2026">November MPC Summary and minutes</a></td>
        </tr>
        <tr>
            <td>Thursday&nbsp;17 December</td>
            <td><a href="/x">December MPC Summary and minutes</a></td>
        </tr>
    </tbody>
</table>
<h2>2028 provisional dates</h2>
<table><tbody><tr><td>Thursday 3 February</td><td>February</td></tr></tbody></table>
"""

BOJ = """
<table>
<caption class="non-caption">Table : 2026</caption>
<tbody>
    <tr>
        <td><a href="/en/mopo/mpmdeci/mpr_2026/k261030a.pdf">Oct. 29 (Thurs.), 30 (Fri.) [PDF 172KB]</a></td>
        <td>-</td>
    </tr>
    <tr>
        <td>Dec. 17 (Thurs.), 18 (Fri.)</td>
        <td>-</td>
    </tr>
</tbody>
</table>
<table>
<caption class="non-caption">Table : 2027</caption>
<tbody>
    <tr>
        <td>Apr. 30 (Fri.), May 1 (Sat.)</td>
        <td>-</td>
    </tr>
</tbody>
</table>
"""

BOC = """
<article class="media" id='post-1'>
    <div class="media-body">
        <span class='bocss-margin-left-medium media-date pull-right'>November 12, 2026</span>
        <h3 class="media-heading">
            <a href="https://www.bankofcanada.ca/2026/11/speech/">Speech by the Governor</a>
        </h3>
        <div class='media-excerpt'><p>12:15 (ET)</p></div>
    </div>
</article>
<article class="media" id='post-247290'>
    <div class="media-body">
        <span class='bocss-margin-left-medium media-date pull-right'>December 9, 2026</span>
        <h3 class="media-heading">
            <a href="https://www.bankofcanada.ca/2026/12/interest-rate-announcement-december-9-2026/" data-content-type="Upcoming events">Interest Rate Announcement</a>
        </h3>
        <div class='media-meta'>
        </div>
        <div class='media-excerpt'>
            <p>09:45 (ET)<br />
On eight scheduled dates each year, the Bank of Canada announces the setting for the overnight rate target</p>
        </div>
    </div>
</article>
"""

RBA = """
<table class="table-linear">
    <caption class="no-wrap">
        Board meeting schedules 2026
    </caption>
    <tbody>
        <tr>
            <th colspan="3">January</th>
        </tr>
        <tr>
            <th scope="row">February</th>
            <td>2&ndash;3 February</td>
            <td></td>
        </tr>
        <tr>
            <th scope="row">March</th>
            <td>31 March&ndash;1 April</td>
            <td>5 March</td>
        </tr>
        <tr>
            <th scope="row">December</th>
            <td>7&ndash;8 December</td>
            <td></td>
        </tr>
    </tbody>
</table>
"""


def _days(df):
    return [str(d) for d in df["due_at"]]


# ---- the pages ----------------------------------------------------------------

def test_the_fomc_is_dated_by_its_last_day_at_two_in_new_york():
    df = calendars.parse_fomc(FOMC)
    # The unscheduled meeting is skipped; Apr/May 30-1 is 1 May; 14:00 ET.
    assert _days(df) == ["2026-09-16 18:00:00", "2026-10-28 18:00:00",
                         "2026-12-09 19:00:00", "2027-05-01 18:00:00"]
    assert set(df["release_name"]) == {"FOMC decision"}


def test_the_ecb_decides_on_a_policy_meetings_second_day():
    df = calendars.parse_ecb_meetings(ECB)
    assert _days(df) == ["2026-12-17 13:15:00"]            # 14:15 Frankfurt


def test_the_bank_of_england_takes_confirmed_dates_only():
    df = calendars.parse_boe_mpc(BOE)
    assert _days(df) == ["2026-11-05 12:00:00", "2026-12-17 12:00:00"]


def test_the_bank_of_japan_carries_a_month_over_and_into_the_next():
    df = calendars.parse_boj_mpm(BOJ)
    assert _days(df) == ["2026-10-30 03:00:00", "2026-12-18 03:00:00", "2027-05-01 03:00:00"]


def test_the_bank_of_canada_states_its_time_and_other_events_are_not_decisions():
    df = calendars.parse_boc_rate(BOC)
    assert _days(df) == ["2026-12-09 14:45:00"]            # 09:45 Toronto, from the page


def test_the_rba_decides_on_the_last_day_even_across_a_month():
    df = calendars.parse_rba_board(RBA)
    assert _days(df) == ["2026-02-03 03:30:00", "2026-04-01 03:30:00", "2026-12-08 03:30:00"]


def test_every_meeting_feed_is_fetched_and_named():
    for feed in releases.MEETING_FEEDS:
        meta = releases.FEEDS[feed]
        assert feed in calendars.FETCHERS and meta["fetched"]
        assert meta["decision"] and meta["label"] and meta["time"]
        assert meta["reflected_days"] is not None or meta.get("related"), feed


def test_a_bank_page_that_drops_past_decisions_keeps_them():
    con = duckdb.connect(":memory:")
    store.init_db(con)
    now = datetime(2026, 11, 5, 13, tzinfo=UTC)
    first = calendars.parse_fomc(FOMC)
    store.replace_release_calendar(con, "fomc_calendar", calendars._frame("fomc_calendar", first, now),
                                   keep_before=first["due_at"].min())
    # The next read lists only December onwards, and December has moved a day
    # later: the old date goes, the past stays, and so does nothing else ahead.
    later = calendars._rows([("FOMC decision", datetime(2026, 12, 10, 19), None)])
    store.replace_release_calendar(con, "fomc_calendar", calendars._frame("fomc_calendar", later, now),
                                   keep_before=datetime(2026, 11, 5, 13))
    got = [str(r[0]) for r in con.execute(
        "SELECT due_at FROM release_calendar ORDER BY due_at").fetchall()]
    assert got == ["2026-09-16 18:00:00", "2026-10-28 18:00:00", "2026-12-10 19:00:00"]


def test_sync_feed_keeps_a_banks_past_but_replaces_a_statistics_feed(monkeypatch):
    con = duckdb.connect(":memory:")
    store.init_db(con)
    monkeypatch.setitem(calendars.FETCHERS, "fomc_calendar", lambda: calendars.parse_fomc(FOMC))
    assert calendars.sync_feed(con, "fomc_calendar")[1] == "ok"
    monkeypatch.setitem(calendars.FETCHERS, "fomc_calendar",
                        lambda: calendars._rows([("FOMC decision", datetime(2026, 12, 9, 19), None)]))
    name, status, detail = calendars.sync_feed(con, "fomc_calendar")
    assert status == "ok" and detail == "1 decision dates"
    assert con.execute("SELECT count(*) FROM release_calendar").fetchone()[0] == 3


# ---- what the decisions make due --------------------------------------------------

def _store(sid, frequency, rows, feed, dates):
    con = duckdb.connect(":memory:")
    store.init_db(con)
    store.upsert_macro_catalog(con, pd.DataFrame([{
        "series_id": sid, "source": "x", "source_code": "x", "name": sid,
        "frequency": frequency, "staleness_days": 450 if frequency == "event" else 10}]))
    store.upsert_macro_series(con, sid, pd.DataFrame(
        {"date": [date.fromisoformat(d) for d, _ in rows], "value": [v for _, v in rows]}))
    df = calendars._rows([(releases.FEEDS[feed]["decision"], d, None) for d in dates])
    store.replace_release_calendar(con, feed, calendars._frame(feed, df, datetime(2026, 9, 1, tzinfo=UTC)))
    return con


FED_DATES = [datetime(2026, 9, 16, 18), datetime(2026, 10, 28, 18), datetime(2026, 12, 9, 19)]


def _fed(*rows):
    return _store("us_fed_target_lower", "daily", rows or [("2026-10-27", 3.75)],
                  "fomc_calendar", FED_DATES)


def test_before_a_decision_the_target_is_fresh_and_names_the_next():
    a = freshness.assess(_fed(("2026-10-29", 3.75)), "us_fed_target_lower",
                         now=datetime(2026, 11, 5, 13, tzinfo=UTC))
    assert a["status"] == "fresh"
    nr = a["next_release"]
    assert nr["source"] == "meetings" and nr["approximate"] and nr["date_exact"]
    assert nr["due_at"] == "2026-12-09T14:00:00-05:00"
    assert nr["message"] == ("Next: FOMC decision, 9 Dec 2026, usually 14:00 EST "
                             "(the Fed's FOMC meeting calendar).")


def test_after_a_decision_the_target_is_due_until_the_next_day_is_held():
    now = datetime(2026, 10, 29, 15, tzinfo=UTC)
    # Data to the decision day itself: the New York Fed dates a new target
    # from the next day, so the decision is not shown yet.
    a = freshness.assess(_fed(("2026-10-28", 3.75)), "us_fed_target_lower", now=now)
    assert a["status"] == "due"
    assert a["next_release"]["message"] == (
        "The FOMC decision of 28 Oct 2026 is not in the store yet (the New York Fed dates a "
        "new target from the day after the decision); data to 28 Oct 2026 is the latest held.")
    a = freshness.assess(_fed(("2026-10-29", 3.75)), "us_fed_target_lower", now=now)
    assert a["status"] == "fresh" and "9 Dec 2026" in a["next_release"]["message"]


def test_a_decision_that_never_shows_turns_late():
    # Decided Wednesday 28 Oct; Thursday's target is published on Friday, and
    # two days' grace after that runs to Sunday afternoon in New York.
    a = freshness.assess(_fed(("2026-10-28", 3.75)), "us_fed_target_lower",
                         now=datetime(2026, 11, 1, 12, tzinfo=UTC))
    assert a["status"] == "due"
    a = freshness.assess(_fed(("2026-10-28", 3.75)), "us_fed_target_lower",
                         now=datetime(2026, 11, 2, 12, tzinfo=UTC))
    assert a["status"] == "late" and "has not reached Bedrok" in a["next_release"]["message"]


def test_staleness_still_overrides_a_decision():
    a = freshness.assess(_fed(("2026-10-01", 3.75)), "us_fed_target_lower",
                         now=datetime(2026, 11, 5, 13, tzinfo=UTC))
    assert a["status"] == "stale"


def _ecb(*rows):
    return _store("ecb_deposit_rate", "event", rows or [("2026-06-17", 2.25)],
                  "ecb_meetings", [datetime(2026, 10, 29, 13, 15), datetime(2026, 12, 17, 13, 15)])


def test_a_hold_on_a_change_only_series_shows_once_it_is_fetched_after_it_takes_effect():
    con = _ecb()
    now = datetime(2026, 11, 6, 9, tzinfo=UTC)
    a = freshness.assess(con, "ecb_deposit_rate", now=now)
    assert a["status"] == "due"
    assert a["next_release"]["message"] == (
        "The ECB monetary policy decision of 29 Oct 2026 is not in the store yet (new rates take "
        "effect the following Wednesday); the latest rate held dates from 17 Jun 2026.")
    store.record_check(con, "ecb_deposit_rate", datetime(2026, 11, 2, 9))   # before it took effect
    assert freshness.assess(con, "ecb_deposit_rate", now=now)["status"] == "due"
    assert freshness.assess(con, "ecb_deposit_rate",
                            now=datetime(2026, 11, 8, 9, tzinfo=UTC))["status"] == "late"
    store.record_check(con, "ecb_deposit_rate", datetime(2026, 11, 4, 9))   # the Wednesday
    a = freshness.assess(con, "ecb_deposit_rate", now=now)
    assert a["status"] == "fresh" and "17 Dec 2026" in a["next_release"]["message"]


def test_a_change_shows_without_a_check():
    con = _ecb(("2026-06-17", 2.25), ("2026-11-04", 2.0))
    a = freshness.assess(con, "ecb_deposit_rate", now=datetime(2026, 11, 6, 9, tzinfo=UTC))
    assert a["status"] == "fresh"


def test_a_decision_more_than_a_month_old_no_longer_makes_a_rate_due():
    a = freshness.assess(_ecb(), "ecb_deposit_rate", now=datetime(2026, 12, 2, 9, tzinfo=UTC))
    assert a["status"] == "fresh"


def test_a_store_without_the_new_tables_judges_a_policy_rate_by_age():
    con = _ecb()
    con.execute("DROP TABLE release_calendar")
    con.execute("DROP TABLE series_checks")
    a = freshness.assess(con, "ecb_deposit_rate", now=datetime(2026, 11, 6, 9, tzinfo=UTC))
    assert a["status"] == "fresh" and a["next_release"] is None


def test_a_fetch_without_error_is_recorded(monkeypatch):
    con = duckdb.connect(":memory:")
    store.init_db(con)
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": s, "source": "ecb", "source_code": s, "name": s, "frequency": "event",
         "filled_from": f"ecb:{s}", "cache": "store"} for s in ("ecb_deposit_rate", "ecb_mro_rate")]))

    def fetch(row, start):
        if row["series_id"] == "ecb_mro_rate":
            raise OSError("503")
        return pd.DataFrame(columns=["date", "value"])        # a hold: nothing new
    monkeypatch.setattr(pipeline, "_fetch_macro", fetch)
    monkeypatch.setattr(pipeline, "_pace", lambda s: None)
    pipeline.sync_macro(con, incremental=True, prime=False)
    got = dict(con.execute("SELECT series_id, checked_at FROM series_checks").fetchall())
    assert set(got) == {"ecb_deposit_rate"}


# ---- the calendar tool -----------------------------------------------------------

@pytest.fixture
def cal(tmp_path, monkeypatch):
    """A store with the Fed's target, the Bank of England's rate and every
    schedule stubbed: the BoE decided this morning and the store ends a week ago."""
    db = tmp_path / "market.duckdb"
    monkeypatch.setattr(config, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", db)
    con = duckdb.connect(str(db))
    store.init_db(con)
    today = date.today()
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": s, "source": src, "source_code": s, "name": name, "frequency": "daily",
         "units": "percent", "license": "attribution", "cache": "store", "staleness_days": 10}
        for s, src, name in (("us_fed_target_lower", "nyfed", "US federal funds target (lower)"),
                             ("us_fed_target_upper", "nyfed", "US federal funds target (upper)"),
                             ("uk_boe_bank_rate", "boe", "UK Official Bank Rate"))]))
    for s, days in (("us_fed_target_lower", 1), ("us_fed_target_upper", 1), ("uk_boe_bank_rate", 7)):
        store.upsert_macro_series(con, s, pd.DataFrame(
            {"date": [today - pd.Timedelta(days=days)], "value": [3.75]}))
    store.close_checkpointed(con)
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    stubs = {
        "fomc_calendar": [now + pd.Timedelta(days=20)],
        "boe_mpc": [now - pd.Timedelta(hours=2), now + pd.Timedelta(days=40)],
        "boj_mpm": [now + pd.Timedelta(days=30)],
    }
    for feed in calendars.FETCHERS:
        dates = stubs.get(feed, [])
        rows = [(releases.FEEDS[feed].get("decision", "x"), d, None) for d in dates]
        monkeypatch.setitem(calendars.FETCHERS, feed,
                            lambda rows=rows: calendars._rows(rows) if rows else
                            calendars._rows([("x", datetime(2000, 1, 1), None)]))
    mcp_server._last_attempt.clear()


def _call(name, args):
    async def go():
        async with Client(mcp_server.server) as c:
            return await c.call_tool(name, args)
    return anyio.run(go).structured_content


def test_the_week_lists_the_banks_and_a_due_rate_first(cal):
    out = _call("release_calendar", {"days": 35})
    rows = out["releases"]
    boe = rows[0]
    assert boe["status"] == "due" and boe["series"] == ["uk_boe_bank_rate"]
    assert boe["name"] == "Bank of England MPC decision"
    fomc = next(r for r in rows if r["name"] == "FOMC decision")
    assert fomc["status"] == "upcoming" and fomc["approximate"]
    assert fomc["series"] == ["us_fed_target_lower", "us_fed_target_upper"]   # one line
    boj = next(r for r in rows if r["name"] == "Bank of Japan policy decision")
    assert boj["series"] == [] and "jp_policy_rate is the BIS month-end rate" in boj["message"]


def test_asking_for_a_policy_rate_names_its_banks_next_decision(cal):
    out = _call("release_calendar", {"series_ids": ["us_fed_target_upper"]})
    s = out["series"][0]
    assert s["status"] == "upcoming" and s["period"].startswith("FOMC decision of ")
