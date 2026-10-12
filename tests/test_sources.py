"""The source list (bedrok/seeds/sources.csv) is the one place a publisher is
described. These keep it complete and consistent with the catalog."""
import duckdb
import pandas as pd
import pytest

from bedrok.core import config, licensing, sources, store
from bedrok.ingest import pipeline


@pytest.fixture(scope="module")
def rows():
    return sources.load()


@pytest.fixture(scope="module")
def catalog():
    return pd.read_csv(pipeline.CATALOG_CSV, dtype=str)


def test_every_catalog_and_fx_source_is_in_the_list(rows, catalog):
    used = set(catalog["source"]) | {src for _, src, _ in config.fx_pairs()}
    assert used - set(rows) == set()


def test_every_row_says_how_to_reach_it(rows):
    for s, r in rows.items():
        assert r["protocol"] in {"sdmx", "pxweb", "adapter", "bulk"}, s
        if r["protocol"] == "sdmx":
            assert r["base_url"].startswith("https://"), s
            assert r["format"] in {"accept", "csvdata", "xml"}, s
        if r["protocol"] == "pxweb":
            assert r["base_url"].startswith("https://"), s
            assert r["format"] in {"v1", "v2", "statbank"}, s
        if r["protocol"] == "adapter":
            assert r["adapter"], s
        assert r["user_agent"] in {"browser", "default"}, s


def test_every_row_carries_its_terms(rows):
    for s, r in rows.items():
        assert r["license"] in licensing.LICENSE_VALUES, s
        assert r["cache"] in licensing.CACHE_VALUES, s
        assert r["attribution"].strip(), f"{s}: no attribution line"
        if s != "derived":
            assert r["terms_url"].startswith(("https://", "http://")), f"{s}: no terms URL"


def test_keys_are_named_consistently(rows):
    for s, r in rows.items():
        if r["key_env"]:
            assert r["key_env"].endswith("_API_KEY"), s
            assert r["key_required"] in {"yes", "optional"}, s
        else:
            assert r["key_required"] == "no", s


def test_every_smoke_series_is_a_catalog_row_of_its_own_source(rows, catalog):
    by_id = catalog.set_index("series_id")["source"]
    for s, r in rows.items():
        if r["smoke_series"]:
            assert by_id.get(r["smoke_series"]) == s, f"{s}: smoke_series {r['smoke_series']}"
        elif r["protocol"] != "bulk":
            pytest.fail(f"{s}: a fetched source needs a smoke_series")


def test_every_fetched_source_has_a_fetcher(rows):
    fetched = {s for s, r in rows.items() if r["protocol"] in ("sdmx", "pxweb", "adapter")}
    assert set(pipeline.MACRO_FETCHERS) == fetched
    assert set(pipeline.BULK_SOURCES) == {s for s, r in rows.items() if r["protocol"] == "bulk"}


def test_a_required_key_that_is_missing_skips_without_a_network_call(monkeypatch):
    monkeypatch.delenv("BEA_API_KEY", raising=False)
    with pytest.raises(pipeline.MissingKey, match="no BEA_API_KEY"):
        pipeline.MACRO_FETCHERS["bea"]("T20804:1:M", None)


def test_redact_removes_a_key_from_an_error_message(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "abcdef0123456789")
    err = RuntimeError("400 for url: https://api.stlouisfed.org/x?api_key=abcdef0123456789&y=1")
    assert "abcdef0123456789" not in pipeline.error_detail(err, 200)
    assert "<redacted>" in pipeline.error_detail(err, 200)


def test_licensing_views_come_from_the_list(rows):
    for s, r in rows.items():
        assert licensing.ATTRIBUTIONS[s] == r["attribution"]
        assert licensing.SOURCE_DEFAULT_LICENSE[s] == r["license"]


def test_retired_series_are_removed_and_stay_out(catalog):
    assert not set(catalog["series_id"]) & pipeline.RETIRED
    con = duckdb.connect(":memory:")
    store.init_db(con)
    store.upsert_macro_catalog(con, pd.DataFrame([{"series_id": "wb_barley", "source": "worldbank",
                                                   "source_code": "x", "name": "Barley"}]))
    store.upsert_macro_series(con, "wb_barley", pd.DataFrame(
        {"date": [pd.Timestamp("2020-08-01").date()], "value": [1.0]}))
    pipeline.load_catalog(con)
    assert con.execute("SELECT count(*) FROM macro_series WHERE series_id = 'wb_barley'").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM macro_catalog WHERE series_id = 'wb_barley'").fetchone()[0] == 0
