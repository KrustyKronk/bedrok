"""The MCP server, in memory, against a temporary store with stubbed publishers."""
import time
from datetime import date, timedelta

import anyio
import duckdb
import pandas as pd
import pytest
from mcp import Client

from bedrok.core import config, store
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
