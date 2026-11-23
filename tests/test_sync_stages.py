"""The sync stages end to end, and `backfill`'s choice of stages (STATE.md item 12).

An in-memory store and inline payloads in place of the publishers: the stages
run their real code (catalog query, incremental start, upsert or replace,
derived arithmetic, FX mirror and inverses, the report rows) with only the
network call swapped. No recorded cassettes, by project decision; whether the
publishers answer is the liveness run's job.
"""
import sys
from datetime import date

import duckdb
import pandas as pd
import pytest

from bedrok.core import config, store
from bedrok.ingest import derived, pipeline
from bedrok.ingest.adapters import cftc

DAYS = pd.bdate_range("2026-01-05", "2026-03-31")


def _frame(dates, values):
    return pd.DataFrame({"date": pd.to_datetime(dates).date, "value": values})


def _series(start_value, step=0.0):
    return _frame(DAYS, [start_value + step * i for i in range(len(DAYS))])


# Each code a fake publisher knows, and what it answers.
PAYLOADS = {
    "Y10": _series(4.0, 0.001), "Y2": _series(3.5),
    "EXR/D.USD.EUR.SP00.A": _series(1.10), "EXR/D.GBP.EUR.SP00.A": _series(0.85),
    "H10:RXI$US_N.B.UK": _series(1.30), "fx:5": _series(500.0), "fx:6": _series(550.0),
}


@pytest.fixture
def calls():
    """(source, code, start) of every fetch the stages made."""
    return []


@pytest.fixture
def con(monkeypatch, calls):
    c = duckdb.connect(":memory:")
    store.init_db(c)
    store.upsert_macro_catalog(c, pd.DataFrame([
        {"series_id": "us_10y", "source": "fedboard", "source_code": "Y10", "name": "US 10y",
         "frequency": "daily", "units": "percent", "license": "public_domain", "cache": "store"},
        {"series_id": "us_2y", "source": "fedboard", "source_code": "Y2", "name": "US 2y",
         "frequency": "daily", "units": "percent", "license": "public_domain", "cache": "store"},
        {"series_id": "us_vix", "source": "fred", "source_code": "VIXCLS", "name": "VIX",
         "frequency": "daily", "units": "index", "license": "attribution_fred_channel",
         "cache": "session"},
    ]))
    def fetcher(source):
        def fetch(code, start):
            calls.append((source, code, start))
            if code == "BROKEN":
                raise ValueError("the publisher answered 404")
            df = PAYLOADS.get(code, _frame([], []))
            return df if start is None else df[pd.to_datetime(df["date"]) >= pd.Timestamp(start)]
        return fetch

    monkeypatch.setattr(pipeline, "MACRO_FETCHERS",
                        {s: fetcher(s) for s in ("fedboard", "ecb", "nbk", "fred")})
    monkeypatch.setattr(pipeline, "MACRO_PRIMERS", {})
    monkeypatch.setattr(pipeline, "_pace", lambda source: None)
    monkeypatch.setattr(config, "BASE_CCY", "EUR")
    yield c
    c.close()


def _n(con, sid):
    return con.execute("SELECT count(*) FROM macro_series WHERE series_id = ?", [sid]).fetchone()[0]


def _status(report):
    return {name: status for name, status, _ in report}


def test_macro_then_derived_then_fx_fill_a_store_from_nothing(con, calls):
    # A session-only row that was once stored loses its history, unfetched.
    store.upsert_macro_series(con, "us_vix", _frame(["2025-01-02"], [15.0]))

    macro = pipeline.sync_macro(con, incremental=True)
    assert _status(macro) == {"us_vix": "ok", "us_10y": "ok", "us_2y": "ok"}
    assert _n(con, "us_vix") == 0 and not any(s == "fred" for s, _, _ in calls)
    assert _n(con, "us_10y") == len(DAYS)
    # A first pull is a full one, and records where the history came from.
    assert all(start is None for _, _, start in calls)
    assert con.execute("SELECT filled_from FROM macro_catalog WHERE series_id = 'us_10y'"
                       ).fetchone()[0] == "fedboard:Y10"

    der = derived.sync_derived(con)
    assert _status(der)["us_10y2y"] == "ok"
    assert _status(der)["us_breakeven_10y"] == "skip"         # awaiting us_10y_real
    first = con.execute("SELECT value FROM macro_series WHERE series_id = 'us_10y2y' "
                        "ORDER BY date LIMIT 1").fetchone()[0]
    assert first == pytest.approx(0.5)
    assert con.execute("SELECT name FROM macro_catalog WHERE series_id = 'us_10y2y'"
                       ).fetchone()[0] == "US 10y minus 2y term spread"

    fx = pipeline.sync_fx(con)
    assert all(status == "ok" for _, status, _ in fx), fx
    # Every pair lands in fx_rates and is mirrored as a series; inverses are
    # computed from the stored rate, never fetched.
    assert _n(con, "eurusd") == len(DAYS) and _n(con, "usdkzt") == len(DAYS)
    usdeur = con.execute("SELECT value FROM macro_series WHERE series_id = 'usdeur' "
                         "LIMIT 1").fetchone()[0]
    assert usdeur == pytest.approx(1 / 1.10)
    assert not any(code.startswith("1/") for _, code, _ in calls)


def test_an_incremental_run_overlaps_and_duplicates_nothing(con, calls):
    pipeline.sync_macro(con)
    calls.clear()
    pipeline.sync_macro(con, incremental=True)
    starts = {code: start for _, code, start in calls}
    last = DAYS[-1].date()
    # Re-fetched from a window before the last stored date, never from scratch.
    assert starts["Y10"] is not None and pd.Timestamp(starts["Y10"]).date() < last
    assert _n(con, "us_10y") == len(DAYS)


def test_a_repointed_series_is_replaced_whole_and_a_failed_one_keeps_its_history(con):
    pipeline.sync_macro(con)
    store.upsert_macro_series(con, "us_2y", _frame(["2020-01-01"], [9.9]))   # an old source's row
    con.execute("UPDATE macro_catalog SET filled_from = 'boe:OLD' WHERE series_id = 'us_2y'")
    con.execute("UPDATE macro_catalog SET filled_from = 'fedboard:Y10' WHERE series_id = 'us_10y'")
    con.execute("UPDATE macro_catalog SET source_code = 'BROKEN' WHERE series_id = 'us_10y'")
    report = dict((n, (s, d)) for n, s, d in pipeline.sync_macro(con, incremental=True))

    # us_2y: pulled in full and swapped, so no row of the old source survives.
    assert report["us_2y"][0] == "ok" and "replaced boe:OLD" in report["us_2y"][1]
    assert _n(con, "us_2y") == len(DAYS)
    assert con.execute("SELECT filled_from FROM macro_catalog WHERE series_id = 'us_2y'"
                       ).fetchone()[0] == "fedboard:Y2"
    # us_10y: re-pointed to a code that fails; the stored history stays.
    assert report["us_10y"][0] == "FAIL" and "404" in report["us_10y"][1]
    assert _n(con, "us_10y") == len(DAYS)


def test_a_missing_key_skips_without_a_call(con, monkeypatch):
    def no_key(code, start):
        raise pipeline.MissingKey("FEDBOARD_KEY is not set")
    monkeypatch.setitem(pipeline.MACRO_FETCHERS, "fedboard", no_key)
    assert _status(pipeline.sync_macro(con)) == {"us_10y": "skip", "us_2y": "skip"}
    assert _n(con, "us_10y") == 0


def _cot_payload(years):
    weeks = pd.date_range("2026-01-06", periods=8, freq="W-TUE")
    return {sid: _frame(weeks, [1000.0 * (i + 1)] * len(weeks))
            for i, sid in enumerate(cftc.MARKETS)}, []


def test_the_cot_stage_writes_every_market_and_then_pulls_only_this_year(con, monkeypatch):
    asked = []
    monkeypatch.setattr(cftc, "fetch_cot", lambda years: asked.append(years) or _cot_payload(years))
    report = pipeline.sync_cot(con, incremental=True)
    assert _status(report) == {sid: "ok" for sid in cftc.MARKETS}
    # Nothing stored yet: every year from the first, whatever `incremental` says.
    assert asked[0][0] == cftc.EARLIEST_YEAR and asked[0][-1] == date.today().year
    assert con.execute("SELECT count(*) FROM macro_catalog WHERE series_id LIKE 'cot_%'"
                       ).fetchone()[0] == len(cftc.MARKETS)

    pipeline.sync_cot(con, incremental=True)
    assert asked[1][-1] == date.today().year and len(asked[1]) <= 2
    assert _n(con, "cot_wti_net_noncomm") == 8


def test_a_failed_cot_download_is_one_fail_row_and_writes_nothing(con, monkeypatch):
    def down(years):
        raise ConnectionError("cftc.gov did not answer")
    monkeypatch.setattr(cftc, "fetch_cot", down)
    assert pipeline.sync_cot(con) == [("cftc_cot", "FAIL", "ConnectionError: cftc.gov did not answer")]
    assert con.execute("SELECT count(*) FROM macro_series").fetchone()[0] == 0


# ---- backfill: which stages, in which order, how deep ------------------------

@pytest.fixture
def backfill_run(monkeypatch):
    from bedrok.ingest import backfill
    c = duckdb.connect(":memory:")
    store.init_db(c)
    monkeypatch.setattr(config, "connect", lambda *a, **k: c)
    monkeypatch.setattr(store, "close_checkpointed", lambda con: None)
    monkeypatch.setattr(pipeline, "load_catalog", lambda con: None)
    ran = []

    def stage(name):
        def fn(con, incremental=None, **kw):
            ran.append((name, incremental, kw.get("only")))
            return [(name, "ok", "")]
        return fn
    for attr, name in (("sync_macro", "macro"), ("sync_hicp_europe", "hicp"),
                       ("sync_eurostat_multi", "eurostat"), ("sync_world", "world"),
                       ("sync_pink_sheet", "pinksheet"), ("sync_cot", "cot"),
                       ("sync_fx", "fx")):
        monkeypatch.setattr(pipeline, attr, stage(name))
    monkeypatch.setattr(derived, "sync_derived", stage("derived"))

    def run(*argv):
        ran.clear()
        monkeypatch.setattr(sys, "argv", ["bedrok backfill", *argv])
        code = backfill.main()
        return code, list(ran)
    return run


def test_backfill_runs_every_stage_deep_with_derived_after_the_fetches(backfill_run):
    code, ran = backfill_run()
    assert code == 0
    assert [n for n, _, _ in ran] == ["macro", "hicp", "eurostat", "world", "pinksheet",
                                      "cot", "derived", "fx"]
    assert all(inc is False for _, inc, _ in ran)


def test_backfill_stage_picks_whole_stages_in_their_own_order(backfill_run):
    _, ran = backfill_run("--stage", "fx", "--stage", "cot")
    assert [n for n, _, _ in ran] == ["cot", "fx"]


def test_backfill_only_runs_the_per_series_loop_for_those_ids(backfill_run):
    _, ran = backfill_run("--only", "us_10y, us_2y,")
    assert ran == [("macro", False, {"us_10y", "us_2y"})]
