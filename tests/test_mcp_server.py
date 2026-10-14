"""The MCP server, in memory, against a temporary store with stubbed publishers."""
import time
from datetime import date, timedelta

import anyio
import duckdb
import pandas as pd
import pytest
from mcp import Client

from bedrok.core import charting, config, store
from bedrok.ingest import pipeline
from bedrok.surfaces import mcp_server

TODAY = date.today()


def _frame(dates, values):
    return pd.DataFrame({"date": pd.to_datetime(dates).date, "value": values})


def _monthly(n, end, start_value=100.0):
    dates = pd.date_range(end=pd.Timestamp(end).replace(day=1), periods=n, freq="MS")
    return _frame(dates, [start_value + i for i in range(n)])


@pytest.fixture
def srv(tmp_path, monkeypatch):
    """A store with one fresh series, one stale one and one session-only one."""
    db = tmp_path / "market.duckdb"
    monkeypatch.setattr(config, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setenv("FRED_API_KEY", "test-key-123456")
    for src in ("FRED", "BIS", "FEDBOARD"):
        monkeypatch.setenv(f"BEDROK_PACE_{src}", "0")
    con = duckdb.connect(str(db))
    store.init_db(con)
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "us_cpi", "source": "fedboard", "source_code": "X:CPI", "name": "US CPI",
         "frequency": "monthly", "units": "index", "notes": "a test index", "license": "public_domain",
         "cache": "store"},
        {"series_id": "jp_policy_rate", "source": "bis", "source_code": "WS_CBPOL/M.JP",
         "name": "Japan central bank policy rate (BIS)", "frequency": "monthly", "units": "percent",
         "notes": "month-end value", "license": "attribution", "cache": "store"},
        {"series_id": "us_vix", "source": "fred", "source_code": "VIXCLS", "name": "CBOE VIX",
         "frequency": "daily", "units": "index", "notes": "live only", "license": "attribution_fred_channel",
         "cache": "session"},
    ]))
    store.replace_macro_series(con, "us_cpi", _monthly(36, TODAY), "fedboard:X:CPI")
    store.replace_macro_series(con, "jp_policy_rate",
                               _frame(["2020-01-01", "2020-02-01"], [0.1, 0.1]), "bis:WS_CBPOL/M.JP")
    store.close_checkpointed(con)

    calls = []

    def fake(source):
        def fetch(code, start):
            calls.append((source, code, start))
            if source == "bis":
                return _frame(["2020-01-01", "2020-02-01", (TODAY - timedelta(days=20)).isoformat()],
                              [0.1, 0.1, 1.25])
            if source == "fred":
                return _frame(["2026-01-02", "2026-01-05", "2026-01-06"], [15.0, 20.0, 18.0])
            return _monthly(36, TODAY)
        return fetch
    for s in ("bis", "fred", "fedboard"):
        monkeypatch.setitem(pipeline.MACRO_FETCHERS, s, fake(s))
    mcp_server._last_attempt.clear()
    mcp_server._job.clear()
    mcp_server._job.update(status="idle")
    return calls


def _call(name, args=None):
    async def go():
        async with Client(mcp_server.server) as c:
            return await c.call_tool(name, args or {})
    return anyio.run(go)


def _rows(con_path, sid):
    con = duckdb.connect(str(con_path), read_only=True)
    try:
        return con.execute("SELECT count(*) FROM macro_series WHERE series_id = ?", [sid]).fetchone()[0]
    finally:
        con.close()


def test_nine_tools_and_only_refresh_writes():
    async def go():
        async with Client(mcp_server.server) as c:
            return (await c.list_tools()).tools
    tools = {t.name: t for t in anyio.run(go)}
    assert set(tools) == {"list_sources", "search_series", "list_series", "describe_series",
                          "get_series", "chart_series", "percentile", "query", "refresh"}
    assert [n for n, t in tools.items() if not t.annotations.read_only_hint] == ["refresh"]


def test_a_stale_series_is_refetched_before_it_is_answered(srv):
    r = _call("get_series", {"series_ids": ["jp_policy_rate"]})
    s = r.structured_content["series"][0]
    assert s["fetched"].startswith("refreshed now")
    assert s["observations"][-1]["value"] == 1.25
    assert ("bis", "WS_CBPOL/M.JP") in [c[:2] for c in srv]
    assert "attribution" in s and s["caveats"] == "month-end value"


def test_a_fresh_series_is_answered_from_the_store(srv):
    r = _call("get_series", {"series_ids": ["us_cpi"], "transform": "yoy"})
    s = r.structured_content["series"][0]
    assert s["fetched"] == "store" and s["units"].startswith("percent change")
    assert srv == []
    assert s["observations"][-1]["value"] == pytest.approx((135 / 123 - 1) * 100)


def test_a_session_series_is_fetched_live_and_never_written(srv):
    r = _call("get_series", {"series_ids": ["us_vix"]})
    s = r.structured_content["series"][0]
    assert "not stored" in s["fetched"] and len(s["observations"]) == 3
    assert _rows(config.DB_PATH, "us_vix") == 0


def test_percentile_always_carries_its_sample(srv):
    r = _call("percentile", {"series_id": "us_vix"}).structured_content
    assert r["sample"] == {"start": "2026-01-02", "end": "2026-01-06", "n": 3, "years": 0.0}
    assert r["short_sample"] is True


def test_query_is_one_read_only_statement_without_file_access(srv):
    ok = _call("query", {"sql": "SELECT series_id FROM macro_catalog ORDER BY 1"})
    assert ok.structured_content["rows"] == [["jp_policy_rate"], ["us_cpi"], ["us_vix"]]
    for sql in ("SELECT 1; SELECT 2", "DELETE FROM macro_series",
                "SELECT * FROM read_csv('/etc/hosts')"):
        assert _call("query", {"sql": sql}).is_error, sql


def test_unknown_series_is_a_readable_error(srv):
    r = _call("describe_series", {"series_id": "nope"})
    assert r.is_error and "search_series" in r.content[0].text


def test_a_store_held_by_another_process_reads_as_busy(srv, monkeypatch):
    def locked(read_only=False):
        raise duckdb.IOException('Could not set lock on file "market.duckdb": Conflicting lock')
    monkeypatch.setattr(config, "connect", locked)
    r = _call("describe_series", {"series_id": "us_cpi"})
    assert r.is_error and "busy" in r.content[0].text


def test_search_finds_by_words_in_the_name(srv):
    r = _call("search_series", {"text": "japan policy rate"}).structured_content
    assert r["matches"][0]["series_id"] == "jp_policy_rate"


def test_list_sources_says_whether_a_key_is_set_never_what_it_is(srv):
    out = _call("list_sources").structured_content["sources"]
    fred = next(s for s in out if s["source"] == "fred")
    assert fred["key_set"] is True and "test-key-123456" not in str(out)


def test_refresh_runs_in_the_background_and_reports_progress(srv, monkeypatch):
    monkeypatch.setattr(pipeline, "load_catalog", lambda con: None)
    monkeypatch.setattr(pipeline, "REFRESH_STAGES",
                        [pipeline.REFRESH_STAGES[0], ("noop", "Nothing", lambda con, incremental: [])])
    started = _call("refresh").structured_content
    assert started["mode"] == "background" and started["status"] == "running"
    for _ in range(100):
        if mcp_server._job.get("status") != "running":
            break
        time.sleep(0.05)
    assert mcp_server._job["status"] == "done"
    assert mcp_server._job["total"] == 3          # two stored series + one bulk stage
    assert mcp_server._job["failures"] == []
    # a read during or after the job still works
    assert not _call("describe_series", {"series_id": "us_cpi"}).is_error


def test_refresh_of_named_series_happens_now(srv):
    r = _call("refresh", {"series_ids": ["jp_policy_rate", "us_vix", "nope"]}).structured_content
    status = {x["series_id"]: x["status"] for x in r["results"]}
    assert status == {"jp_policy_rate": "ok", "us_vix": "not stored", "nope": "unknown id"}


def test_nothing_reaches_stdout(srv, capfd):
    _call("get_series", {"series_ids": ["jp_policy_rate", "us_vix"]})
    _call("refresh", {"series_ids": ["us_cpi"]})
    assert capfd.readouterr().out == ""


def _add_rows(rows):
    con = duckdb.connect(str(config.DB_PATH))
    store.upsert_macro_catalog(con, pd.DataFrame(rows))
    store.close_checkpointed(con)


def test_describe_lists_related_series_and_credits_a_computed_series_inputs(srv):
    _add_rows([
        {"series_id": "us_10y", "source": "fedboard", "source_code": "H15:X", "name": "US 10y",
         "notes": "parts: us_10y_real and us_acm_tp10", "license": "public_domain"},
        {"series_id": "us_10y_real", "source": "fedboard", "source_code": "H15:Y", "name": "US 10y real",
         "license": "public_domain"},
        {"series_id": "us_acm_tp10", "source": "fedfiles", "source_code": "acm:ACMTP10",
         "name": "ACM term premium", "license": "attribution"},
        {"series_id": "us_breakeven_10y", "source": "derived", "source_code": "diff:us_10y-us_10y_real",
         "name": "US 10y breakeven", "license": "attribution"},
    ])
    d = _call("describe_series", {"series_id": "us_10y"}).structured_content
    assert {"us_breakeven_10y", "us_10y_real", "us_acm_tp10"} <= {r["series_id"] for r in d["related"]}
    b = _call("describe_series", {"series_id": "us_breakeven_10y"}).structured_content
    assert "Board of Governors of the Federal Reserve System" in b["attribution"]
    assert "National Bureau" not in b["attribution"]


def test_a_supplied_value_has_no_date_of_its_own(srv):
    r = _call("percentile", {"series_id": "us_cpi", "value": 120.0}).structured_content
    assert r["value"] == 120.0 and r["value_date"] is None and r["is_latest"] is False


def test_a_missing_key_says_which_key_and_where_to_get_it(srv, monkeypatch):
    from bedrok.core import sources
    monkeypatch.setenv("FRED_API_KEY", "${user_config.fred_api_key}")   # a blank bundle field
    # the real fetcher, which refuses before any network call when the key is unset
    monkeypatch.setitem(pipeline.MACRO_FETCHERS, "fred",
                        pipeline._fetcher("fred", sources.get("fred")))
    r = _call("get_series", {"series_ids": ["us_vix"]}).structured_content["series"][0]
    assert "FRED_API_KEY" in r["error"] and "fredaccount.stlouisfed.org" in r["error"]


def test_warnings_ride_on_answers_by_severity_and_period(srv, monkeypatch):
    from bedrok.core import warnings
    month = (TODAY.replace(day=1) - timedelta(days=200)).replace(day=1)
    rows = tuple({**{c: "" for c in warnings.COLUMNS}, **r, "series_ids": ("us_cpi",)} for r in (
        {"warning_id": "gap", "country": "United States", "severity": "material",
         "period_start": month.strftime("%Y-%m"), "period_end": month.strftime("%Y-%m"),
         "series": "us_cpi"},
        {"warning_id": "note", "country": "United States", "severity": "caveat",
         "period_start": "2025-08", "series": "us_cpi"},
    ))
    monkeypatch.setattr(warnings, "load", lambda: rows)

    def ids(ws):
        return [w["warning_id"] for w in ws]
    s = _call("get_series", {"series_ids": ["us_cpi"]}).structured_content["series"][0]
    assert ids(s["warnings"]) == ["gap"]                 # loud, inside; the caveat stays out
    later = (month + timedelta(days=62)).isoformat()
    s = _call("get_series", {"series_ids": ["us_cpi"], "start": later}).structured_content["series"][0]
    assert s["warnings"] == []                           # outside the rows sent
    p = _call("percentile", {"series_id": "us_cpi"}).structured_content
    assert ids(p["warnings"]) == ["gap"]
    d = _call("describe_series", {"series_id": "us_cpi"}).structured_content
    assert set(ids(d["warnings"])) == {"gap", "note"}


def test_search_answers_with_warnings_for_a_country_bedrok_does_not_carry(srv, monkeypatch):
    from bedrok.core import warnings
    row = {**{c: "" for c in warnings.COLUMNS}, "warning_id": "ar_cpi", "country": "Argentina",
           "severity": "disqualifying", "concept": "cpi inflation", "period_start": "2007",
           "period_end": "2015", "series_ids": ()}
    monkeypatch.setattr(warnings, "load", lambda: (row,))
    r = _call("search_series", {"text": "argentina inflation"}).structured_content
    assert r["matches"] == [] and [w["warning_id"] for w in r["warnings"]] == ["ar_cpi"]


def test_ordinary_words_and_family_countries_find_their_series(srv):
    """The 2026-10-12 self-test: "trade balance" returned no series because a
    warning's country mentioned "trade"; "turkey inflation" found nothing
    although Bedrok stores Türkiye's HICP. Against the real warnings seed."""
    con = duckdb.connect(str(config.DB_PATH))
    store.upsert_macro_catalog(con, pd.DataFrame([
            {"series_id": "us_trade_balance", "source": "census", "source_code": "x",
             "name": "US trade balance goods and services", "frequency": "monthly",
             "units": "millions USD"},
            {"series_id": "hicp_yoy_tr", "source": "eurostat", "source_code": "x",
             "name": "HICP annual rate — Türkiye", "frequency": "monthly", "units": "percent"},
            {"series_id": "ecb_deposit_rate", "source": "ecb", "source_code": "x",
             "name": "ECB deposit facility rate", "frequency": "event", "units": "percent"}]))
    store.close_checkpointed(con)

    def found(text):
        r = _call("search_series", {"text": text}).structured_content
        return [m["series_id"] for m in r["matches"]], [w["warning_id"] for w in r["warnings"]]
    assert found("trade balance") == (["us_trade_balance"], [])
    series, warned = found("turkey inflation")
    assert series == ["hicp_yoy_tr"] and "tr_cpi_item_prices_2022" in warned
    assert "ecb_deposit_rate" in found("euro area deposit rate")[0]


def test_series_of_different_frequencies_come_back_on_one_calendar(srv):
    """Session 8: the assistant asks for a frequency instead of lining rows up."""
    r = _call("get_series", {"series_ids": ["us_vix", "us_cpi"], "frequency": "weekly"}).structured_content
    t = r["table"]
    assert t["dates"] == ["2026-01-02", "2026-01-09"]        # Fridays over the daily series' span
    assert t["columns"]["us_vix"] == [15.0, 18.0]            # each week's last value
    assert t["stepped"] == {"us_vix": False, "us_cpi": True}
    jan = t["columns"]["us_cpi"]
    assert jan[0] == jan[1] and jan[0] is not None           # January's value repeated, not invented
    assert t["how"] == "last"
    assert all("observations" not in s for s in r["series"])  # the numbers are sent once


def test_a_monthly_series_averages_into_quarters(srv):
    r = _call("get_series", {"series_ids": ["us_cpi"], "frequency": "quarterly"}).structured_content
    t = r["table"]
    assert t["how"] == "mean"                                # monthly into quarters: averaged
    assert all(d.endswith(("-01-01", "-04-01", "-07-01", "-10-01")) for d in t["dates"])


def test_a_cut_says_which_end_and_how_to_get_the_rest(srv):
    s = _call("get_series", {"series_ids": ["us_cpi"], "max_rows": 5}).structured_content["series"][0]
    assert s["truncated"] and len(s["observations"]) == 5
    assert "oldest 31 of 36" in s["cut"] and "frequency" in s["cut"]


def test_a_chart_recipe_puts_different_units_on_two_scales(srv):
    """Session 9: Bedrok specifies the chart; the host only draws it."""
    r = _call("chart_series", {"series_ids": ["us_cpi", "jp_policy_rate"]}).structured_content
    c = r["chart"]
    assert c["axes"]["left"]["series"] == ["us_cpi"] and c["axes"]["right"]["series"] == ["jp_policy_rate"]
    by = {s["id"]: s for s in c["series"]}
    assert by["jp_policy_rate"]["dashed"] and not by["us_cpi"]["dashed"]
    assert c["title"] == "US CPI and Japan central bank policy rate (BIS)"
    assert c["subtitle"].startswith("Left: index. Right: percent; monthly;")   # nothing averaged
    assert r["table"]["frequency"] == "monthly"            # auto: the fastest series' own
    assert c["source"].startswith("Source: ") and c["layout"]["gaps"].startswith("leave empty")
    assert len(c["x"]["labels"]) == len(r["table"]["dates"])


def test_a_daily_series_charts_weekly_and_a_slower_one_is_footnoted_as_a_step(srv):
    r = _call("chart_series", {"series_ids": ["us_vix", "us_cpi"]}).structured_content
    assert r["table"]["frequency"] == "weekly"
    c = r["chart"]
    assert c["axes"]["right"] is None                      # both "index" in the fixture
    assert any("US CPI" in n and "step" in n for n in c["footnotes"])
    assert "fetched live, not stored" in c["source"]


def test_a_place_in_the_search_narrows_it_and_two_letters_match_no_substring(srv):
    """Session 11: "japan rate" is a filter plus a word; "us" is not in usdcad."""
    r = _call("search_series", {"text": "japan policy rate"}).structured_content
    assert [m["series_id"] for m in r["matches"]] == ["jp_policy_rate"]
    us = [m["series_id"] for m in _call("search_series", {"text": "us"}).structured_content["matches"]]
    assert "jp_policy_rate" not in us and set(us) <= {"us_cpi", "us_vix"}


def test_list_series_walks_and_summarises_the_catalog(srv):
    s = _call("list_series", {"summary": True}).structured_content
    assert s["total"] == 3 and s["by_geography"] == {"us": 2, "jp": 1}
    page = _call("list_series", {"limit": 2}).structured_content
    assert [x["series_id"] for x in page["series"]] == ["jp_policy_rate", "us_cpi"]
    assert page["next_offset"] == 2
    rest = _call("list_series", {"limit": 2, "offset": 2}).structured_content
    assert [x["series_id"] for x in rest["series"]] == ["us_vix"] and rest["next_offset"] is None
    assert [x["series_id"] for x in _call("list_series", {"geography": "japan"})
            .structured_content["series"]] == ["jp_policy_rate"]


def test_an_alias_answers_with_its_canonical_series(srv, monkeypatch):
    from bedrok.core import aliases
    monkeypatch.setitem(aliases.ALIASES, "cpi_us", "us_cpi")
    a = _call("get_series", {"series_ids": ["cpi_us"], "max_rows": 3}).structured_content["series"][0]
    b = _call("get_series", {"series_ids": ["us_cpi"], "max_rows": 3}).structured_content["series"][0]
    assert a["alias_of"] == "us_cpi" and a["observations"] == b["observations"]
    d = _call("describe_series", {"series_id": "cpi_us"}).structured_content
    assert d["alias_of"] == "us_cpi" and d["freshness"]["series_id"] == "us_cpi"


def test_everyday_words_find_series_named_otherwise(srv):
    """"inflation" finds a series named CPI; a place Bedrok lacks finds none."""
    r = _call("search_series", {"text": "us inflation"}).structured_content
    assert [m["series_id"] for m in r["matches"]][0] == "us_cpi"


def test_dates_read_one_way_everywhere_and_refusals_say_why(srv):
    """The 2026-10-12 self-test: percentile(since="2010") crashed with no
    message while get_series took "2010"; a start after the end came back
    empty without a word; a malformed date crashed the whole call."""
    first = _call("get_series", {"series_ids": ["us_cpi"], "max_rows": 1000}) \
        .structured_content["series"][0]["observations"][0]["date"]
    year = first[:4]
    p = _call("percentile", {"series_id": "us_cpi", "since": year}).structured_content
    assert p["sample"]["start"] == first
    for tool, args in [("percentile", {"series_id": "us_cpi", "since": "2020-13-01"}),
                       ("get_series", {"series_ids": ["us_cpi"], "start": "2020-13-01"}),
                       ("chart_series", {"series_ids": ["us_cpi"], "start": "March 2020"})]:
        r = _call(tool, args)
        assert r.is_error and "YYYY-MM-DD" in r.content[0].text, tool
    r = _call("get_series", {"series_ids": ["us_cpi"], "start": "2025-12-31", "end": "2025-01-01"})
    assert r.is_error and "after" in r.content[0].text
    r = _call("percentile", {"series_id": "us_cpi", "since": "2099"})
    assert r.is_error and "no observations on or after" in r.content[0].text


def test_a_series_no_scale_can_take_is_marked_not_drawn_and_described_nowhere(srv):
    """The 2026-10-12 self-test: with a third unit, `problem` said a series was
    not placed while `series` put it on the left axis, and a host following
    `series` would have drawn dollars on the percent scale."""
    con = duckdb.connect(str(config.DB_PATH))
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "us_oil", "source": "fedboard", "source_code": "X:OIL", "name": "Test oil",
         "frequency": "monthly", "units": "USD per barrel", "license": "public_domain",
         "cache": "store"}]))
    store.replace_macro_series(con, "us_oil", _monthly(36, TODAY), "fedboard:X:OIL")
    store.close_checkpointed(con)
    c = _call("chart_series", {"series_ids": ["us_cpi", "jp_policy_rate", "us_oil"]}) \
        .structured_content["chart"]
    assert "us_oil is not placed" in c["axes"]["problem"]
    oil = next(s for s in c["series"] if s["id"] == "us_oil")
    assert oil["axis"] is None and oil["drawn"] is False and c["not_drawn"] == ["us_oil"]
    assert "Test oil" not in c["title"]
    assert [s["colour"] for s in c["series"]][:2] == [charting.colour(0), charting.colour(1)]


def _add_rows(rows):
    con = duckdb.connect(str(config.DB_PATH))
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"source": "eurostat", "source_code": "x", "frequency": "annual", "units": "percent", **r}
        for r in rows]))
    store.close_checkpointed(con)


def test_parts_of_growth_show_only_when_the_words_ask_for_parts(srv):
    """Session C: 180 contribution rows would bury the headline in every
    "spain growth"; they answer "spain growth by component" or "what is
    driving spain's growth"."""
    _add_rows([
        {"series_id": "gdp_annual_es", "name": "Real GDP growth, annual — Spain"},
        {"series_id": "gdp_contrib_hh_es", "units": "percentage points",
         "name": "Contribution of household consumption to annual GDP growth — Spain"},
        {"series_id": "gdp_contrib_exp_es", "units": "percentage points",
         "name": "Contribution of exports to annual GDP growth — Spain"}])

    def ids(text):
        return [m["series_id"] for m in
                _call("search_series", {"text": text}).structured_content["matches"]]
    assert ids("spain gdp growth") == ["gdp_annual_es"]
    assert "gdp_contrib_hh_es" in ids("spain gdp growth by component")
    assert "gdp_contrib_exp_es" in ids("what is driving spain gdp growth")


def test_an_empty_search_names_the_nearest_series(srv):
    """The 2026-10-12 logs: an empty list read as "Bedrok has nothing"."""
    r = _call("search_series", {"text": "japan cpi"}).structured_content
    assert r["matches"] == [] and r["nearest"][0] == "us_cpi" and "elsewhere" in r["note"]
    r = _call("search_series", {"text": "argentina inflation"}).structured_content
    assert "nearest" not in r                      # the warning is the answer there


def test_a_gdp_total_and_its_parts_point_at_each_other(srv):
    _add_rows([
        {"series_id": "gdp_annual_es", "name": "Real GDP growth, annual — Spain"},
        {"series_id": "gdp_contrib_hh_es", "units": "percentage points",
         "name": "Contribution of household consumption to annual GDP growth — Spain"},
        {"series_id": "gdp_contrib_net_es", "source": "derived", "units": "percentage points",
         "name": "Contribution of net trade to annual GDP growth — ES"}])
    d = _call("describe_series", {"series_id": "gdp_annual_es"}).structured_content
    assert {"gdp_contrib_hh_es", "gdp_contrib_net_es"} <= {r["series_id"] for r in d["related"]}
    d = _call("describe_series", {"series_id": "gdp_contrib_hh_es"}).structured_content
    assert "gdp_annual_es" in {r["series_id"] for r in d["related"]}


def test_list_series_gives_the_first_date_too(srv):
    s = _call("list_series", {"prefix": "us_cpi"}).structured_content["series"][0]
    assert s["first_date"] and s["first_date"] <= s["last_date"]


def test_contributions_chart_as_stacked_bars_on_one_scale_with_their_total(srv):
    """Session C: the canonical picture of "what drove growth". Parts in
    percentage points and the total in percent share one scale here, and a
    footnote says whether the parts add up."""
    con = duckdb.connect(str(config.DB_PATH))
    rows, years = [], ["2024-01-01", "2025-01-01"]
    series = {"gdp_annual_es": ("Real GDP growth, annual — Spain", "percent", [3.7, 2.6]),
              "gdp_contrib_hh_es": ("Contribution of household consumption to annual GDP growth — Spain",
                                    "percentage points", [1.74, 1.70]),
              "gdp_contrib_net_es": ("Contribution of net trade to annual GDP growth — ES",
                                     "percentage points", [0.32, -0.84]),
              "gdp_contrib_rest_es": ("Contribution of everything else to annual GDP growth — Spain",
                                      "percentage points", [1.64, 1.74])}
    for sid, (name, units, vals) in series.items():
        rows.append({"series_id": sid, "source": "eurostat", "source_code": "x", "name": name,
                     "frequency": "annual", "units": units, "staleness_days": 9999, "cache": "store"})
        store.replace_macro_series(con, sid, _frame(years, vals), "eurostat:x")
    store.upsert_macro_catalog(con, pd.DataFrame(rows))
    store.close_checkpointed(con)
    ids = ["gdp_contrib_hh_es", "gdp_contrib_net_es", "gdp_contrib_rest_es", "gdp_annual_es",
           "us_cpi", "jp_policy_rate", "gdp_contrib_hh_es"]
    assert not _call("chart_series", {"series_ids": ids[:7]}).is_error    # seven accepted
    c = _call("chart_series", {"series_ids": ids[:4]}).structured_content["chart"]
    assert c["form"] == "stacked_bars" and c["total"] == "gdp_annual_es"
    assert c["title"] == "Real GDP growth, annual — Spain, by contribution"
    assert c["axes"]["right"] is None                                   # one scale
    assert {s["id"]: s["kind"] for s in c["series"]}["gdp_contrib_net_es"] == "part"
    assert any("add up to the total within 0.05" in n for n in c["footnotes"])
    lines = _call("chart_series", {"series_ids": ["us_cpi"]}).structured_content["chart"]
    assert lines["form"] == "lines" and lines["total"] is None


def test_a_transform_per_series_puts_a_rate_beside_an_index_in_one_recipe(srv):
    """Session D1: the self-test merged two calls by hand because one transform
    applied to every series. A published rate beside an index charted as a
    rate is now one recipe, on one scale."""
    c = _call("chart_series", {"series_ids": ["jp_policy_rate", "us_cpi"],
                               "transforms": {"us_cpi": "yoy"}, "frequency": "monthly"}) \
        .structured_content
    by = {s["series_id"]: s for s in c["series"]}
    assert by["us_cpi"]["transform"] == "yoy" and by["jp_policy_rate"]["transform"] == "level"
    assert by["us_cpi"]["units"] == "percent change on a year earlier"
    assert by["us_cpi"]["catalog_units"] == "index"
    assert c["chart"]["axes"]["right"] is None
    g = _call("get_series", {"series_ids": ["us_cpi"], "transform": "index_to",
                             "index_date": "2025-02-15", "frequency": "quarterly"}).structured_content
    assert g["series"][0]["units"] == "index 2025Q1=100"


def test_outside_rows_are_drawn_in_house_style_and_named_as_outside(srv):
    """Session D1: what Bedrok does not carry may be drawn beside what it
    does, labelled. Before, a mixed chart fell back to hand styling and
    labelled nothing (the self-test's Spain breakdown and Japan GDP)."""
    extra = [{"label": "Japan real GDP growth", "units": "percent", "source": "World Bank",
              "frequency": "annual",
              "observations": [{"date": "2023-01-01", "value": 0.7}, {"date": "2024-01-01", "value": -0.2},
                               {"date": "2025-01-01", "value": 1.2}]}]
    r = _call("chart_series", {"series_ids": ["jp_policy_rate"], "extra": extra, "frequency": "annual"}) \
        .structured_content
    c = r["chart"]
    jp = next(s for s in c["series"] if s["id"] == "extra:Japan real GDP growth")
    assert jp["drawn"] and jp["colour"] == charting.colour(1) and jp["kind"] is None
    assert "World Bank (fetched by Claude, not via Bedrok)" in c["source"]
    assert any("supplied by the assistant" in n for n in c["footnotes"])
    at = r["table"]["dates"].index("2025-01-01")
    assert r["table"]["columns"]["extra:Japan real GDP growth"][at] == 1.2
    too_many = _call("chart_series", {"series_ids": ["us_cpi"] * 5, "extra": extra * 3})
    assert too_many.is_error and "seven" in too_many.content[0].text


def test_a_chart_of_a_rate_says_so_in_its_title(srv):
    c = _call("chart_series", {"series_ids": ["us_cpi"], "transform": "yoy"}).structured_content["chart"]
    assert c["title"] == "US CPI, annual rate"


def test_many_series_at_one_date_are_bars(srv):
    """Session D1: the self-test's debt ranking had no recipe and was drawn
    by hand; `at` gives a bar per series, the last value on or before it."""
    at = TODAY.isoformat()
    r = _call("chart_series", {"series_ids": ["us_cpi", "jp_policy_rate"], "at": at}).structured_content
    assert r["chart"]["form"] == "bars" and r["table"]["at"] == at
    rows = {x["id"]: x for x in r["table"]["rows"]}
    assert rows["jp_policy_rate"]["value"] == 1.25                  # refetched, 20 days old
    assert rows["jp_policy_rate"]["date"] == (TODAY - timedelta(days=20)).isoformat()
    assert r["chart"]["x"]["labels"] == ["US CPI", "Japan central bank policy rate (BIS)"]
    assert any("Older than the date asked" in n for n in r["chart"]["footnotes"])
    assert r["chart"]["axes"]["right"]["series"] == ["jp_policy_rate"]    # index vs percent


def test_parts_come_along_in_one_call(srv):
    """Item 17: a yield with its real yield, breakeven and term premium was
    four calls; with_related makes it one, and describe says which related
    series are parts."""
    con = duckdb.connect(str(config.DB_PATH))
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "us_10y", "source": "fedboard", "source_code": "X:10", "name": "US 10-year yield",
         "frequency": "monthly", "units": "percent", "cache": "store",
         "notes": "benchmark long rate. Its parts: us_10y_real (TIPS) and us_breakeven_10y; see also us_cpi"},
        {"series_id": "us_10y_real", "source": "fedboard", "source_code": "X:10R", "name": "US 10-year real",
         "frequency": "monthly", "units": "percent", "cache": "store"},
        {"series_id": "us_breakeven_10y", "source": "fedboard", "source_code": "X:BE", "name": "US breakeven",
         "frequency": "monthly", "units": "percent", "cache": "store"}]))
    for sid in ("us_10y", "us_10y_real", "us_breakeven_10y"):
        store.replace_macro_series(con, sid, _monthly(36, TODAY), f"fedboard:{sid}")
    store.close_checkpointed(con)
    d = _call("describe_series", {"series_id": "us_10y"}).structured_content
    roles = {r["series_id"]: r["role"] for r in d["related"]}
    assert roles["us_10y_real"] == "part" and roles["us_breakeven_10y"] == "part"
    assert roles["us_cpi"] == "companion"
    g = _call("get_series", {"series_ids": ["us_10y"], "with_related": True}).structured_content
    assert [s["series_id"] for s in g["series"]] == ["us_10y", "us_10y_real", "us_breakeven_10y"]
    assert g["expanded_from"] == {"us_10y": ["us_10y_real", "us_breakeven_10y"]} and g["dropped"] == []
    c = _call("chart_series", {"series_ids": ["us_10y"], "with_related": True}).structured_content
    assert len(c["chart"]["series"]) == 3 and c["expanded_from"]["us_10y"]


def test_a_money_level_percentile_carries_its_caveat(srv):
    con = duckdb.connect(str(config.DB_PATH))
    store.upsert_macro_catalog(con, pd.DataFrame([
        {"series_id": "us_m2", "source": "fedboard", "source_code": "X:M2", "name": "US M2",
         "frequency": "monthly", "units": "billions USD", "cache": "store"}]))
    store.replace_macro_series(con, "us_m2", _monthly(36, TODAY), "fedboard:X:M2")
    store.close_checkpointed(con)
    assert "grows with the economy" in _call("percentile", {"series_id": "us_m2"}).structured_content["caveat"]
    assert "caveat" not in _call("percentile", {"series_id": "us_cpi"}).structured_content
