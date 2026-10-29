"""The six FX pairs answer as series: sync_fx mirrors fx_rates into the catalog."""
from datetime import date, timedelta

import anyio
import duckdb
import pandas as pd
import pytest
from mcp import Client

from bedrok.core import config, store, units
from bedrok.ingest import pipeline
from bedrok.surfaces import mcp_server

TODAY = date.today()


def _daily(n, end, start=1.10):
    dates = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    return pd.DataFrame({"date": dates.date, "value": [start + i / 1000 for i in range(n)]})


def _fake_fetchers(monkeypatch, calls):
    def fake(source):
        def fetch(code, start):
            calls.append((source, code))
            return _daily(30, TODAY - timedelta(days=1), 1.25 if source == "fedboard" else 1.10)
        return fetch
    for s in ("ecb", "fedboard", "nbk"):
        monkeypatch.setitem(pipeline.MACRO_FETCHERS, s, fake(s))
        monkeypatch.setenv(f"BEDROK_PACE_{s.upper()}", "0")


def test_the_six_rows_speak_the_vocabulary_and_name_their_convention():
    rows = {r["series_id"]: r for r in pipeline.fx_catalog_rows()}
    assert set(rows) == set(pipeline.FX_IDS)
    assert {p.lower() for p, _, _ in config.fx_pairs()} == set(rows)
    assert rows["eurusd"]["units"] == "USD per EUR"       # CCY per 1 EUR, rule 5
    assert rows["usdeur"]["units"] == "EUR per USD"
    assert all(units.valid(r["units"]) for r in rows.values())
    # an inverse is credited to the publisher of the rate it inverts
    assert rows["usdeur"]["source"] == "ecb" and rows["usdgbp"]["source"] == "fedboard"
    assert all(r["staleness_days"] == 14 for r in rows.values())


def test_sync_fx_mirrors_and_the_macro_stage_never_fetches_a_pair(monkeypatch):
    con = duckdb.connect(":memory:")
    store.init_db(con)
    pipeline.load_catalog(con)
    calls = []
    _fake_fetchers(monkeypatch, calls)
    pipeline.sync_fx(con)
    a = con.execute("SELECT date, rate FROM fx_rates WHERE pair = 'USDEUR' ORDER BY date").fetchall()
    b = con.execute("SELECT date, value FROM macro_series WHERE series_id = 'usdeur' "
                    "ORDER BY date").fetchall()
    assert a == b and len(a) == 30
    assert set(pipeline.per_series_catalog(con)["series_id"]).isdisjoint(pipeline.FX_IDS)
    calls.clear()
    pipeline.sync_macro(con, incremental=True, only=set(pipeline.FX_IDS))
    assert calls == []
    stage, rows = pipeline.refresh_series(con, "gbpusd")
    assert stage == "fx" and ("GBPUSD", "ok") in {(n, s) for n, s, _ in rows}


@pytest.fixture
def fx_srv(tmp_path, monkeypatch):
    db = tmp_path / "market.duckdb"
    monkeypatch.setattr(config, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", db)
    calls = []
    _fake_fetchers(monkeypatch, calls)
    con = duckdb.connect(str(db))
    store.init_db(con)
    store.upsert_macro_catalog(con, pd.DataFrame(pipeline.fx_catalog_rows()))
    pipeline.sync_fx(con)
    store.close_checkpointed(con)
    mcp_server._last_attempt.clear()
    return calls


def _call(name, args):
    async def go():
        async with Client(mcp_server.server) as c:
            return (await c.call_tool(name, args)).structured_content
    return anyio.run(go)


def test_a_pair_answers_in_every_series_tool(fx_srv):
    found = _call("search_series", {"text": "eurusd"})
    assert found["matches"][0]["series_id"] == "eurusd"
    d = _call("describe_series", {"series_id": "eurusd"})
    assert d["units"] == "USD per EUR" and d["freshness"]["allowance_days"] == 14
    assert d["freshness"]["status"] == "fresh" and "European Central Bank" in d["publisher"]
    assert {r["series_id"] for r in _call("describe_series", {"series_id": "usdeur"})["related"]} \
        == {"eurusd"}
    g = _call("get_series", {"series_ids": ["EURUSD"]})       # either case answers
    assert g["series"][0]["alias_of"] == "eurusd" and len(g["series"][0]["observations"]) == 30
    p = _call("percentile", {"series_id": "gbpusd"})
    assert p["sample"]["n"] == 30 and "money" not in str(p.get("caveats", "")).lower()
    c = _call("chart_series", {"series_ids": ["eurusd", "usdeur"]})
    assert c["series"][0]["units"] == "USD per EUR"


def test_a_place_does_not_bring_the_pairs_into_a_search(fx_srv):
    assert _call("search_series", {"text": "euro area exchange rate"})["matches"] == []


def test_the_tenge_pairs_are_the_national_banks_own_rates_with_no_inverse():
    """Session K1: both tenge rates are fetched, so EURKZT needs no cross, and
    neither claims an inverse Bedrok does not carry."""
    from bedrok.core import geography
    rows = {r["series_id"]: r for r in pipeline.fx_catalog_rows()}
    assert rows["usdkzt"]["units"] == "KZT per USD" and rows["eurkzt"]["units"] == "KZT per EUR"
    assert rows["usdkzt"]["source"] == rows["eurkzt"]["source"] == "nbk"
    assert "inverse is" not in rows["usdkzt"]["notes"] and "inverse is" not in rows["eurkzt"]["notes"]
    assert "Its inverse is usdeur" in rows["eurusd"]["notes"]
    assert ("EURKZT", "nbk", "fx:6") in config.fx_pairs()
    # a tenge rate is Kazakhstan's; a euro or dollar rate is still no country's
    assert geography.of_series("usdkzt") == geography.of_series("eurkzt") == "kz"
    assert geography.of_series("eurusd") is None and geography.of_series("usdeur") is None
